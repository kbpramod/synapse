from datetime import datetime
from uuid import uuid4
from typing import Optional, List, Dict, Any
from sqlalchemy.orm import Session

from models.fact import Fact
from src.ai.embedding_service import embedding_service


class FactService:
    """
    Manages engineering facts extracted from PRs, meetings, and documents.
    Distinguishes fact_status (knowledge lifecycle) from work_status (engineering progress).
    """

    async def create_fact(
        self,
        db: Session,
        content: str,
        source_type: str,
        source_id: str,
        repository_id: Optional[str] = None,
        person_id: Optional[str] = None,
        fact_status: str = "ACTIVE",
        work_status: str = "IN_PROGRESS",
        metadata_json: Optional[Dict[str, Any]] = None,
        embedding: Optional[List[float]] = None
    ) -> Fact:
        """Creates a single engineering fact with vector embedding for semantic search."""
        if embedding is None:
            try:
                embedding = await embedding_service.embed(content)
            except Exception as e:
                print(f"[FACT EMBED ERROR] Could not embed fact '{content[:40]}...': {e}")
                embedding = None

        fact = Fact(
            id=str(uuid4()),
            content=content,
            embedding=embedding,
            source_type=source_type,
            source_id=source_id,
            repository_id=repository_id,
            person_id=person_id,
            fact_status=fact_status,
            work_status=work_status,
            metadata_json=metadata_json or {},
            created_at=datetime.utcnow()
        )
        db.add(fact)
        db.flush()
        return fact

    async def create_facts_from_change_record(
        self,
        db: Session,
        change_record: Dict[str, Any],
        pull_request_id: str,
        repository_id: Optional[str] = None,
        person_id: Optional[str] = None,
        base_metadata: Optional[Dict[str, Any]] = None
    ) -> List[Fact]:
        """
        Extracts discrete, granular facts from a structured ChangeRecord:
        - Creates an individual Fact for each architectural / functional change item
        - Creates a high-level summary fact with impact notes
        """
        created_facts: List[Fact] = []
        meta = dict(base_metadata or {})

        changes = change_record.get("changes", [])
        summary = change_record.get("summary", "")
        impact = change_record.get("impact", [])

        # 1. Store individual granular change facts
        for item in changes:
            if isinstance(item, dict):
                area = item.get("area", "General")
                desc = item.get("description", "")
                content = f"[{area}] {desc}"
                item_meta = {**meta, "area": area, "description": desc, "fact_type": "component_change"}
            else:
                content = str(item)
                item_meta = {**meta, "fact_type": "component_change"}

            fact = await self.create_fact(
                db=db,
                content=content,
                source_type="github_pr",
                source_id=pull_request_id,
                repository_id=repository_id,
                person_id=person_id,
                fact_status="ACTIVE",
                work_status="IN_PROGRESS",
                metadata_json=item_meta
            )
            created_facts.append(fact)

        # 2. Store high-level summary fact with impact
        if summary:
            impact_text = f" Impact: {'; '.join(impact)}" if impact else ""
            summary_content = f"PR Summary: {summary}.{impact_text}"
            summary_meta = {**meta, "impact": impact, "fact_type": "pr_summary"}

            summary_fact = await self.create_fact(
                db=db,
                content=summary_content,
                source_type="github_pr",
                source_id=pull_request_id,
                repository_id=repository_id,
                person_id=person_id,
                fact_status="ACTIVE",
                work_status="IN_PROGRESS",
                metadata_json=summary_meta
            )
            created_facts.append(summary_fact)

        return created_facts

    def update_work_status(
        self,
        db: Session,
        source_id: str,
        new_work_status: str
    ) -> int:
        """Updates work_status for all facts associated with a given source_id (e.g. PR merged -> COMPLETED)."""
        facts = db.query(Fact).filter(Fact.source_id == source_id).all()
        for f in facts:
            f.work_status = new_work_status
            f.updated_at = datetime.utcnow()
        db.flush()
        return len(facts)

    def invalidate_or_supersede_facts(
        self,
        db: Session,
        fact_ids: List[str],
        new_status: str = "SUPERSEDED"
    ) -> int:
        """Updates fact_status (e.g. to SUPERSEDED or INVALIDATED) when knowledge is replaced."""
        facts = db.query(Fact).filter(Fact.id.in_(fact_ids)).all()
        for f in facts:
            f.fact_status = new_status
            f.updated_at = datetime.utcnow()
        db.flush()
        return len(facts)


fact_service = FactService()
