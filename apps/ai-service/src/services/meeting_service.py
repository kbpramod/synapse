import logging
from datetime import datetime
from typing import Optional, List, Dict, Any, Tuple
from uuid import uuid4
from sqlalchemy.orm import Session

from db.database import SessionLocal
from models.meeting import Meeting
from models.user import User
from src.models.event_node import EventNode
from src.models.knowledge_node import KnowledgeNode
from src.models.work_item import WorkItem
from src.models.person import Person
from src.schemas.meeting import MeetingSubmitRequest
from src.schemas.event import EventCreate
from src.services.identity_service import identity_service
from src.services.fact_service import fact_service
from src.services.event_service import event_service
from src.ai.llm_service import llm_service
from src.ai.embedding_service import embedding_service
from src.transcription.factory import get_transcript_provider
from src.transcription.utils import parse_google_meet_url
from src.transcription.models import CanonicalTranscript

logger = logging.getLogger(__name__)


class MeetingService:

    async def start_vexa_meeting(
        self,
        db: Session,
        meeting_url: str,
        bot_name: str,
        user: User,
        title: Optional[str] = None
    ) -> Meeting:
        """
        Dispatches a Vexa meeting bot to a Google Meet call and creates the local meeting record.
        """
        native_meeting_id = parse_google_meet_url(meeting_url)
        meeting_title = title or "Engineering Sync"

        provider = get_transcript_provider()
        bot_info = await provider.start_meeting(
            meeting_url=meeting_url,
            native_meeting_id=native_meeting_id,
            title=meeting_title,
            bot_name="Engineering Assistant"
        )

        meeting_id = str(uuid4())
        meeting = Meeting(
            id=meeting_id,
            title=meeting_title,
            platform="google_meet",
            meeting_url=meeting_url,
            native_meeting_id=native_meeting_id,
            vexa_meeting_id=bot_info.bot_id,
            status=bot_info.status or "joining",
            started_at=datetime.utcnow()
        )
        db.add(meeting)
        db.commit()
        db.refresh(meeting)

        logger.info(f"[MEETING CREATED] Started meeting id='{meeting.id}', native_id='{native_meeting_id}', status='{meeting.status}'")
        return meeting

    async def get_canonical_transcript(
        self,
        db: Session,
        meeting_id: str
    ) -> Optional[CanonicalTranscript]:
        """
        Retrieves the canonical transcript for a meeting from the transcription provider.
        """
        meeting = db.query(Meeting).filter(
            (Meeting.id == meeting_id) | (Meeting.native_meeting_id == meeting_id) | (Meeting.external_meeting_id == meeting_id)
        ).first()

        if not meeting:
            return None

        native_id = meeting.native_meeting_id
        if not native_id and meeting.meeting_url:
            try:
                native_id = parse_google_meet_url(meeting.meeting_url)
            except Exception:
                native_id = meeting.id
        elif not native_id:
            native_id = meeting.id

        provider = get_transcript_provider()
        canonical_transcript = await provider.get_transcript(
            native_meeting_id=native_id,
            platform=meeting.platform or "google_meet",
            meeting_id=meeting.id,
            title=meeting.title
        )

        # Update local record with latest participant list / status if active
        if canonical_transcript.segments:
            if meeting.status == "joining":
                meeting.status = "active"
            meeting.participants_json = canonical_transcript.participants
            db.commit()

        canonical_transcript.status = meeting.status
        return canonical_transcript

    def create_meeting_job(
        self,
        db: Session,
        request: MeetingSubmitRequest
    ) -> Meeting:
        """
        Creates an initial Meeting record with status 'PROCESSING'
        and commits immediately so the client can receive HTTP 202 Accepted.
        """
        meeting_id = str(uuid4())
        external_id = request.meeting_id or meeting_id
        title = request.title or f"Engineering Meeting - {datetime.utcnow().strftime('%Y-%m-%d %H:%M')}"

        # Parse participants list into clean string names
        participant_names = []
        for p in request.participants or []:
            if isinstance(p, dict):
                participant_names.append(p.get("name") or p.get("email") or "Unknown")
            elif isinstance(p, str):
                participant_names.append(p)

        # Parse transcript text from segments or raw text
        transcript_text = request.transcript or ""
        if not transcript_text and request.segments:
            transcript_text = "\n".join(
                f"[{s.timestamp or '00:00'}] {s.speaker}: {s.text}"
                for s in request.segments
            )

        # Extract timestamps
        started_at = None
        if request.started_at:
            if isinstance(request.started_at, str):
                try:
                    started_at = datetime.fromisoformat(request.started_at.replace("Z", "+00:00"))
                except Exception:
                    started_at = datetime.utcnow()
            elif isinstance(request.started_at, datetime):
                started_at = request.started_at

        ended_at = None
        if request.ended_at:
            if isinstance(request.ended_at, str):
                try:
                    ended_at = datetime.fromisoformat(request.ended_at.replace("Z", "+00:00"))
                except Exception:
                    ended_at = None
            elif isinstance(request.ended_at, datetime):
                ended_at = request.ended_at

        meeting = Meeting(
            id=meeting_id,
            external_meeting_id=external_id,
            title=title,
            organization_id=request.organization_id,
            repository_id=request.repository_id if request.repository_id != "default" else None,
            status="PROCESSING",
            started_at=started_at or datetime.utcnow(),
            ended_at=ended_at,
            participants_json=participant_names,
            transcript_raw=transcript_text,
            metadata_json=request.metadata or {}
        )
        db.add(meeting)
        db.commit()
        db.refresh(meeting)
        logger.info(f"[MEETING ACCEPTED] Created meeting job id={meeting.id}, title='{meeting.title}'")
        return meeting

    async def process_meeting_job(self, meeting_db_id: str) -> None:
        """
        Background task to process the meeting:
        1. Resolve participant identities to Person entities.
        2. LLM Analysis: summary, decisions, action items, discrete facts.
        3. Discrete Fact extraction with 1536-dim vector embeddings.
        4. WorkItem creation for commitments.
        5. Ingest into EventNode and KnowledgeNode.
        6. Update Meeting record to 'COMPLETED'.
        """
        db = SessionLocal()
        try:
            meeting = db.query(Meeting).filter(Meeting.id == meeting_db_id).first()
            if not meeting:
                logger.error(f"[MEETING ERROR] Meeting {meeting_db_id} not found for background processing.")
                return

            logger.info(f"[MEETING PROCESSING] Starting background analysis for meeting {meeting.id} ('{meeting.title}')...")

            # 1. Resolve participant identities
            participants = meeting.participants_json or []
            person_map: Dict[str, Person] = {}

            for p_name in participants:
                if p_name and p_name.strip():
                    person, _ = identity_service.resolve_identity(
                        db=db,
                        raw_identity=p_name.strip(),
                        provider="meeting"
                    )
                    person_map[p_name.strip().lower()] = person

            # 2. LLM Comprehensive Analysis
            transcript = meeting.transcript_raw or f"Meeting: {meeting.title}"
            analysis = await llm_service.analyze_meeting_comprehensive(
                title=meeting.title,
                transcript_text=transcript,
                participants=participants
            )

            summary = analysis.get("summary") or f"Summary for {meeting.title}"
            decisions = analysis.get("decisions") or []
            action_items = analysis.get("action_items") or []
            discrete_facts = analysis.get("discrete_facts") or []

            # 3. Ingest Facts into 'facts' table with vector embeddings
            created_facts = []

            # A. Process discrete facts from analysis
            for fact_item in discrete_facts:
                content = fact_item.get("content")
                if not content:
                    continue
                
                person_name = fact_item.get("person")
                fact_person_id = None
                if person_name:
                    p = person_map.get(person_name.strip().lower())
                    if not p:
                        p, _ = identity_service.resolve_identity(db, raw_identity=person_name, provider="meeting")
                        person_map[person_name.strip().lower()] = p
                    fact_person_id = p.id

                fact_record = await fact_service.create_fact(
                    db=db,
                    content=content,
                    source_type="meeting",
                    source_id=meeting.id,
                    fact_status=fact_item.get("fact_status", "ACTIVE"),
                    work_status=fact_item.get("work_status", "IN_PROGRESS"),
                    person_id=fact_person_id,
                    repository_id=meeting.repository_id,
                    metadata_json={
                        "meeting_id": meeting.id,
                        "meeting_title": meeting.title,
                        "area": fact_item.get("area", "General")
                    }
                )
                created_facts.append(fact_record)

            # B. If decisions/action_items not in discrete_facts, ensure they have fact rows
            for dec in decisions:
                dec_content = f"[{dec.get('section', 'Architecture')} Decision] {dec.get('decision')}"
                if dec.get('rationale'):
                    dec_content += f" (Rationale: {dec.get('rationale')})"
                
                # Check if already added
                if not any(dec_content in f.content for f in created_facts):
                    f_dec = await fact_service.create_fact(
                        db=db,
                        content=dec_content,
                        source_type="meeting",
                        source_id=meeting.id,
                        fact_status="ACTIVE",
                        work_status="COMPLETED",
                        person_id=None,
                        repository_id=meeting.repository_id,
                        metadata_json={"meeting_id": meeting.id, "topic": dec.get("topic")}
                    )
                    created_facts.append(f_dec)

            # 4. Create WorkItems for Action Items
            for idx, ai in enumerate(action_items):
                assignee_name = ai.get("assignee") or "Unassigned"
                assignee_person = None
                if assignee_name != "Unassigned":
                    assignee_person = person_map.get(assignee_name.strip().lower())
                    if not assignee_person:
                        assignee_person, _ = identity_service.resolve_identity(db, raw_identity=assignee_name, provider="meeting")
                        person_map[assignee_name.strip().lower()] = assignee_person

                work_item = WorkItem(
                    title=f"[{ai.get('area', 'General')}] {ai.get('task')}",
                    description=f"Action item from meeting '{meeting.title}': {ai.get('task')}",
                    assignee_person_id=assignee_person.id if assignee_person else None,
                    status=ai.get("work_status", "PLANNED"),
                    area=ai.get("area", "General"),
                    metadata_json={"meeting_id": meeting.id, "meeting_title": meeting.title}
                )
                db.add(work_item)

                # Ensure action item has discrete fact
                ai_content = f"[Action Item - {assignee_name}] {ai.get('task')}"
                if not any(ai_content in f.content for f in created_facts):
                    f_ai = await fact_service.create_fact(
                        db=db,
                        content=ai_content,
                        source_type="meeting",
                        source_id=meeting.id,
                        fact_status="ACTIVE",
                        work_status=ai.get("work_status", "IN_PROGRESS"),
                        person_id=assignee_person.id if assignee_person else None,
                        repository_id=meeting.repository_id,
                        metadata_json={"meeting_id": meeting.id, "area": ai.get("area")}
                    )
                    created_facts.append(f_ai)

            # 5. Ingest into EventNode & KnowledgeNode (Timeline & RAG)
            ref_id = f"meeting_{meeting.id}_summary"
            event_in = EventCreate(
                source="meeting",
                event_type="meeting_processed",
                timestamp=meeting.started_at or datetime.utcnow(),
                actor=participants[0] if participants else "Meeting Organizer",
                entity_type="meeting",
                entity_id=meeting.title,
                content=f"Meeting '{meeting.title}' processed: {summary}",
                reference_id=ref_id,
                metadata_json={
                    "meeting_id": meeting.id,
                    "decisions_count": len(decisions),
                    "action_items_count": len(action_items),
                    "facts_count": len(created_facts),
                    "participants": participants
                }
            )
            created_event, _ = await event_service.ingest_event(db, event_in)

            # RAG KnowledgeNode Embedding
            summary_emb = await embedding_service.get_embedding(f"{meeting.title}: {summary}")
            kn = KnowledgeNode(
                repository_id=meeting.repository_id or "default",
                section="Meetings",
                topic=meeting.title,
                fact=summary,
                embedding=summary_emb,
                source="meeting",
                event_id=created_event.id if created_event else None
            )
            db.add(kn)

            # 6. Update Meeting Record Status
            meeting.status = "COMPLETED"
            meeting.summary = summary
            meeting.decisions_json = decisions
            meeting.action_items_json = action_items
            meeting.facts_count = len(created_facts)
            db.commit()

            logger.info(f"[MEETING COMPLETED] Processed meeting {meeting.id} ({len(decisions)} decisions, {len(action_items)} action items, {len(created_facts)} facts)")

        except Exception as e:
            db.rollback()
            logger.error(f"[MEETING BACKGROUND PROCESSING FAILED] {e}", exc_info=True)
            try:
                failed_meeting = db.query(Meeting).filter(Meeting.id == meeting_db_id).first()
                if failed_meeting:
                    failed_meeting.status = "FAILED"
                    failed_meeting.error_message = str(e)
                    db.commit()
            except Exception:
                pass
        finally:
            db.close()


meeting_service = MeetingService()
