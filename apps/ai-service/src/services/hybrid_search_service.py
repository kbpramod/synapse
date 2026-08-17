import re
from datetime import datetime, timedelta
from typing import List, Dict, Any, Optional
from sqlalchemy.orm import Session
from sqlalchemy import text, or_

from src.models.event_node import EventNode
from src.models.event_relationship import EventRelationship
from src.models.knowledge_node import KnowledgeNode
from src.models.work_item import WorkItem
from src.ai.embedding_service import embedding_service


class HybridSearchService:

    async def search(
        self,
        db: Session,
        query_text: str,
        time_window_days: Optional[int] = 30,
        actor: Optional[str] = None,
        source: Optional[str] = None,
        top_k: int = 15
    ) -> Dict[str, Any]:
        """
        Execute BM25 + Vector hybrid retrieval with Reciprocal Rank Fusion,
        metadata filtering, and relationship expansion.
        """
        # 1. Calculate time filter threshold
        cutoff_date = None
        if time_window_days:
            cutoff_date = datetime.utcnow() - timedelta(days=time_window_days)

        # 2. Execute BM25 / Keyword Search
        bm25_results = self._bm25_search(
            db=db,
            query_text=query_text,
            cutoff_date=cutoff_date,
            actor=actor,
            source=source,
            limit=top_k * 2
        )

        # 3. Execute Vector Search
        vector_results = await self._vector_search(
            db=db,
            query_text=query_text,
            cutoff_date=cutoff_date,
            limit=top_k * 2
        )

        # 4. Reciprocal Rank Fusion (RRF)
        fused_items = self._reciprocal_rank_fusion(
            bm25_results=bm25_results,
            vector_results=vector_results,
            top_k=top_k
        )

        # 5. Expand Relationships (Traverse connected events)
        expanded_context = self._expand_relationships(
            db=db,
            fused_events=fused_items
        )

        # 6. Fetch related WorkItems
        work_items = self._fetch_related_work_items(
            db=db,
            events=expanded_context
        )

        return {
            "query": query_text,
            "fused_count": len(fused_items),
            "events": expanded_context,
            "work_items": work_items
        }

    def _bm25_search(
        self,
        db: Session,
        query_text: str,
        cutoff_date: Optional[datetime],
        actor: Optional[str],
        source: Optional[str],
        limit: int = 30
    ) -> List[EventNode]:
        """BM25 / Keyword exact search using ILIKE / PostgreSQL full text patterns."""
        keywords = [k.strip() for k in re.findall(r'\w+', query_text) if len(k.strip()) > 2]
        
        query = db.query(EventNode)

        if cutoff_date:
            query = query.filter(EventNode.timestamp >= cutoff_date)

        if actor:
            query = query.filter(EventNode.actor.ilike(f"%{actor}%"))

        if source:
            query = query.filter(EventNode.source == source)

        if keywords:
            or_conditions = []
            for kw in keywords:
                or_conditions.append(EventNode.content.ilike(f"%{kw}%"))
                or_conditions.append(EventNode.entity_id.ilike(f"%{kw}%"))
                or_conditions.append(EventNode.actor.ilike(f"%{kw}%"))
            query = query.filter(or_(*or_conditions))

        results = query.order_by(EventNode.timestamp.desc()).limit(limit).all()
        return results

    async def _vector_search(
        self,
        db: Session,
        query_text: str,
        cutoff_date: Optional[datetime],
        limit: int = 30
    ) -> List[EventNode]:
        """Vector similarity search using pgvector on KnowledgeNode / Event embeddings."""
        try:
            query_embedding = await embedding_service.embed(query_text)
            embedding_str = "[" + ",".join(map(str, query_embedding)) + "]"
        except Exception as e:
            print(f"[VECTOR SEARCH ERROR] Failed to embed query: {e}")
            return []

        # Vector search on knowledge_nodes linked to event_id
        sql = text("""
            SELECT kn.event_id, kn.embedding <=> :embedding AS distance
            FROM knowledge_nodes kn
            WHERE kn.event_id IS NOT NULL
            ORDER BY kn.embedding <=> :embedding
            LIMIT :limit
        """)
        
        try:
            rows = db.execute(sql, {"embedding": embedding_str, "limit": limit}).fetchall()
            event_ids = [row.event_id for row in rows if row.event_id]
            if not event_ids:
                return []
            
            events = db.query(EventNode).filter(EventNode.id.in_(event_ids)).all()
            # Order by score
            event_map = {e.id: e for e in events}
            ordered_events = [event_map[eid] for eid in event_ids if eid in event_map]
            return ordered_events
        except Exception as e:
            print(f"[VECTOR SQL SEARCH ERROR] {e}")
            return []

    def _reciprocal_rank_fusion(
        self,
        bm25_results: List[EventNode],
        vector_results: List[EventNode],
        k: int = 60,
        top_k: int = 15
    ) -> List[EventNode]:
        """Reciprocal Rank Fusion (RRF) to merge BM25 and Vector rankings."""
        scores: Dict[str, float] = {}
        node_map: Dict[str, EventNode] = {}

        # Process BM25 ranks
        for rank, event in enumerate(bm25_results):
            node_map[event.id] = event
            scores[event.id] = scores.get(event.id, 0.0) + (1.0 / (k + rank + 1))

        # Process Vector ranks
        for rank, event in enumerate(vector_results):
            node_map[event.id] = event
            scores[event.id] = scores.get(event.id, 0.0) + (1.0 / (k + rank + 1))

        # Sort by RRF score descending
        sorted_ids = sorted(scores.keys(), key=lambda eid: scores[eid], reverse=True)
        fused = [node_map[eid] for eid in sorted_ids[:top_k]]
        return fused

    def _expand_relationships(
        self,
        db: Session,
        fused_events: List[EventNode]
    ) -> List[EventNode]:
        """Traverse EventRelationships to include connected events (1-hop expansion)."""
        expanded_ids = set(e.id for e in fused_events)
        result_events = list(fused_events)

        if not fused_events:
            return result_events

        event_ids = list(expanded_ids)
        rels = db.query(EventRelationship).filter(
            or_(
                EventRelationship.source_event_id.in_(event_ids),
                EventRelationship.target_event_id.in_(event_ids)
            )
        ).all()

        connected_ids = set()
        for r in rels:
            if r.source_event_id not in expanded_ids:
                connected_ids.add(r.source_event_id)
            if r.target_event_id not in expanded_ids:
                connected_ids.add(r.target_event_id)

        if connected_ids:
            additional_events = db.query(EventNode).filter(EventNode.id.in_(list(connected_ids))).all()
            result_events.extend(additional_events)

        # Sort chronologically by timestamp
        result_events.sort(key=lambda e: e.timestamp or datetime.min)
        return result_events

    def _fetch_related_work_items(
        self,
        db: Session,
        events: List[EventNode]
    ) -> List[WorkItem]:
        """Retrieve any WorkItems directly referenced by the expanded event set."""
        work_item_ids = set(e.work_item_id for e in events if e.work_item_id)
        if not work_item_ids:
            return []
        
        return db.query(WorkItem).filter(WorkItem.id.in_(list(work_item_ids))).all()


hybrid_search_service = HybridSearchService()
