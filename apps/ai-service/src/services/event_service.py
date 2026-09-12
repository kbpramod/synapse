from datetime import datetime
from typing import Optional, List, Tuple
from sqlalchemy.orm import Session

from models.event_node import EventNode
from models.event_relationship import EventRelationship
from models.knowledge_node import KnowledgeNode
from models.work_item import WorkItem
from src.repositories.event_repository import event_repository
from src.repositories.knowledge_repository import knowledge_repository
from src.schemas.event import EventCreate, MeetingIngestRequest
from src.services.identity_service import identity_service
from src.ai.llm_service import llm_service
from src.ai.embedding_service import embedding_service


class EventService:

    async def ingest_event(
        self,
        db: Session,
        event_data: EventCreate
    ) -> Tuple[EventNode, bool]:
        """Idempotently ingest a single event with identity & work item correlation."""
        # 1. Idempotency Check
        if event_data.reference_id:
            existing = event_repository.get_by_reference_id(
                db,
                reference_id=event_data.reference_id,
                source=event_data.source
            )
            if existing:
                print(f"[IDEMPOTENT SKIP] Event with reference_id={event_data.reference_id} already exists.")
                return existing, False

        # 2. Identity Resolution
        person_id = None
        if event_data.actor:
            person, link = identity_service.resolve_identity(
                db=db,
                raw_identity=event_data.actor,
                provider="meeting" if event_data.source == "meeting" else "github"
            )
            person_id = person.id

        # 3. Work Item correlation / linking
        work_item_id = None
        if event_data.entity_id:
            work_item = db.query(WorkItem).filter(
                (WorkItem.title.ilike(f"%{event_data.entity_id}%")) |
                (WorkItem.id == event_data.entity_id)
            ).first()
            if work_item:
                work_item_id = work_item.id

        # 4. Create EventNode
        event = EventNode(
            source=event_data.source,
            event_type=event_data.event_type,
            timestamp=event_data.timestamp or datetime.utcnow(),
            actor=event_data.actor,
            person_id=person_id,
            work_item_id=work_item_id,
            entity_type=event_data.entity_type,
            entity_id=event_data.entity_id,
            content=event_data.content,
            reference_id=event_data.reference_id,
            metadata_json=event_data.metadata_json or {}
        )
        created_event = event_repository.create_event(db, event)

        # 5. Trigger automatic event correlation
        await self.correlate_event(db, created_event)

        return created_event, True

    async def ingest_meeting(
        self,
        db: Session,
        request: MeetingIngestRequest
    ) -> List[EventNode]:
        """Ingest a meeting transcript, resolve identities, extract commitments/decisions, and register work items."""
        # 1. Resolve meeting participants
        for participant in request.participants:
            identity_service.resolve_identity(
                db=db,
                raw_identity=participant,
                provider="meeting"
            )

        segments_dicts = [s.model_dump() for s in request.segments]

        # 2. Extract commitments and decisions via LLM
        extracted_items = await llm_service.extract_meeting_events(
            title=request.title,
            segments=segments_dicts
        )

        ingested_events = []

        for idx, item in enumerate(extracted_items):
            ref_id = f"{request.meeting_id}_evt_{idx}"

            actor_name = item.get("actor")
            person_id = None
            if actor_name:
                person, _ = identity_service.resolve_identity(
                    db=db,
                    raw_identity=actor_name,
                    provider="meeting"
                )
                person_id = person.id

            # 3. Create tracked WorkItem if new commitment
            work_item = None
            topic_title = item.get("topic") or request.title
            if item.get("event_type") == "meeting_commitment":
                work_item = WorkItem(
                    title=topic_title,
                    description=item.get("content"),
                    assignee_person_id=person_id,
                    status="PLANNED",
                    area=item.get("section", "General"),
                    metadata_json={"meeting_id": request.meeting_id}
                )
                db.add(work_item)
                db.commit()
                db.refresh(work_item)

            event_create = EventCreate(
                source="meeting",
                event_type=item.get("event_type", "meeting_commitment"),
                timestamp=datetime.utcnow(),
                actor=actor_name,
                entity_type=item.get("entity_type", "commitment"),
                entity_id=work_item.id if work_item else request.meeting_id,
                content=item.get("content", ""),
                reference_id=ref_id,
                metadata_json={
                    "meeting_id": request.meeting_id,
                    "title": request.title,
                    "section": item.get("section", "General"),
                    "topic": topic_title,
                    "work_item_id": work_item.id if work_item else None
                }
            )

            event_node, created = await self.ingest_event(db, event_create)
            if work_item and event_node:
                event_node.work_item_id = work_item.id
                db.commit()

            ingested_events.append(event_node)

            # 4. Index into Knowledge Base with embedding for pgvector hybrid RAG
            if created and event_node.content:
                try:
                    embedding = await embedding_service.embed(event_node.content)
                    knowledge_node = KnowledgeNode(
                        repository_id=request.repository_id or "default",
                        section=item.get("section", "Meetings"),
                        topic=topic_title,
                        fact=event_node.content,
                        embedding=embedding,
                        source="meeting",
                        event_id=event_node.id
                    )
                    knowledge_repository.create(db, knowledge_node)
                except Exception as e:
                    db.rollback()
                    print(f"[KNOWLEDGE INDEX ERROR] Could not embed meeting event: {e}")

        return ingested_events

    async def correlate_event(
        self,
        db: Session,
        new_event: EventNode
    ) -> List[EventRelationship]:
        """Correlate a new event against candidate past events."""
        created_relationships = []

        candidate_query = db.query(EventNode).filter(
            EventNode.id != new_event.id,
            EventNode.source != new_event.source
        )

        if new_event.person_id:
            candidate_query = candidate_query.filter(
                (EventNode.person_id == new_event.person_id) |
                (EventNode.actor.ilike(f"%{new_event.actor}%"))
            )

        candidates = candidate_query.order_by(EventNode.timestamp.desc()).limit(10).all()

        new_evt_dict = {
            "id": new_event.id,
            "event_type": new_event.event_type,
            "actor": new_event.actor,
            "content": new_event.content
        }

        for cand in candidates:
            cand_dict = {
                "id": cand.id,
                "event_type": cand.event_type,
                "actor": cand.actor,
                "content": cand.content
            }

            eval_result = await llm_service.evaluate_event_correlation(
                event_a=cand_dict,
                event_b=new_evt_dict
            )

            if eval_result.get("is_correlated", False):
                rel_type = eval_result.get("relationship_type", "correlates_to")
                if new_event.source == "github" and cand.source == "meeting":
                    rel_type = "implements"
                
                rel = EventRelationship(
                    source_event_id=cand.id,
                    target_event_id=new_event.id,
                    relationship_type=rel_type,
                    confidence_score=float(eval_result.get("confidence", 1.0))
                )
                saved_rel = event_repository.create_relationship(db, rel)
                created_relationships.append(saved_rel)

                # Update linked WorkItem status if correlation matches
                if cand.work_item_id:
                    work_item = db.query(WorkItem).filter(WorkItem.id == cand.work_item_id).first()
                    if work_item:
                        new_event.work_item_id = work_item.id
                        if new_event.event_type == "pr_created":
                            work_item.status = "AWAITING_REVIEW"
                        elif new_event.event_type == "changes_requested":
                            work_item.status = "CHANGES_REQUESTED"
                        elif new_event.event_type in ("pr_merged", "pr_closed"):
                            work_item.status = "COMPLETED"
                        db.commit()

        return created_relationships

    def get_timeline(
        self,
        db: Session,
        entity_id: Optional[str] = None,
        actor: Optional[str] = None,
        limit: int = 50
    ) -> List[dict]:
        """Build an ordered timeline of events with linked correlation relationships."""
        events = event_repository.list_events(
            db=db,
            actor=actor,
            entity_id=entity_id,
            limit=limit
        )

        timeline = []
        for evt in events:
            rels = event_repository.get_relationships_for_event(db, evt.id)
            related_event_ids = [
                r.target_event_id if r.source_event_id == evt.id else r.source_event_id
                for r in rels
            ]
            related_events = []
            if related_event_ids:
                related_events = db.query(EventNode).filter(EventNode.id.in_(related_event_ids)).all()

            timeline.append({
                "event": evt,
                "relationships": rels,
                "related_events": related_events
            })

        return timeline


event_service = EventService()
