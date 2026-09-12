import logging
from datetime import datetime
from typing import Optional
from uuid import uuid4
from sqlalchemy.orm import Session

from models.meeting import Meeting
from models.decision import Decision
from models.task import Task
from models.knowledge import Knowledge
from src.services.blob_storage import get_blob_storage, BlobStorageService
from src.services.transcript_parser import (
    parse_and_validate_transcript_file,
    validate_pasted_transcript,
    TranscriptParseError
)
from src.services.transcript_analyzer import (
    transcript_analyzer,
    TranscriptAnalyzer,
    TranscriptAnalysisError,
    TranscriptAnalysisOutput
)
from src.schemas.transcript import (
    TranscriptIngestResponse,
    DecisionResponse,
    TaskResponse,
    KnowledgeResponse,
    SourceReferenceResponse
)

logger = logging.getLogger(__name__)


class TranscriptIngestionService:
    """Coordinates transcript ingestion, blob storage, meeting records, and LLM analysis."""

    def __init__(
        self,
        storage_service: Optional[BlobStorageService] = None,
        analyzer: Optional[TranscriptAnalyzer] = None
    ):
        self._storage = storage_service
        self._analyzer = analyzer or transcript_analyzer

    @property
    def storage(self) -> BlobStorageService:
        if self._storage is None:
            self._storage = get_blob_storage()
        return self._storage

    @storage.setter
    def storage(self, value: BlobStorageService) -> None:
        self._storage = value

    async def ingest_pasted_transcript(
        self,
        db: Session,
        title: Optional[str],
        transcript_raw: str,
        user_id: Optional[str] = None,
        org_id: Optional[str] = None
    ) -> TranscriptIngestResponse:
        """
        Ingests pasted raw text transcript:
        1. Validates text
        2. Uploads raw content to blob storage
        3. Creates Meeting record in DB
        4. Triggers analysis pipeline
        """
        cleaned_text = validate_pasted_transcript(transcript_raw)
        meeting_id = str(uuid4())
        meeting_title = (title or "Untitled Meeting").strip() or "Untitled Meeting"

        # 1. Store original text in Blob Storage
        storage_key = f"transcripts/{meeting_id}/transcript.txt"
        data_bytes = cleaned_text.encode("utf-8")
        try:
            uploaded_key = await self.storage.upload(
                key=storage_key,
                data=data_bytes,
                content_type="text/plain; charset=utf-8"
            )
        except Exception as upload_err:
            logger.error(f"[INGESTION ERROR] Blob storage upload failed: {upload_err}", exc_info=True)
            raise RuntimeError(f"Failed to store transcript in object storage: {upload_err}") from upload_err

        # 2. Persist initial Meeting record
        meeting = Meeting(
            id=meeting_id,
            title=meeting_title,
            source_type="pasted",
            storage_key=uploaded_key,
            transcript_text=cleaned_text,
            transcript_raw=cleaned_text,
            organization_id=org_id,
            status="completed",
            analysis_status="processing",
            created_at=datetime.utcnow(),
            updated_at=datetime.utcnow()
        )
        db.add(meeting)
        db.commit()
        db.refresh(meeting)

        # 3. Process analysis pipeline
        return await self.analyze_transcript(db=db, meeting_id=meeting.id)

    async def ingest_file_transcript(
        self,
        db: Session,
        filename: str,
        file_bytes: bytes,
        title: Optional[str] = None,
        user_id: Optional[str] = None,
        org_id: Optional[str] = None
    ) -> TranscriptIngestResponse:
        """
        Ingests uploaded file transcript (.txt, .md, .docx):
        1. Validates extension, size, and extracts text
        2. Uploads raw file binary to blob storage
        3. Creates Meeting record in DB
        4. Triggers analysis pipeline
        """
        cleaned_text = parse_and_validate_transcript_file(filename=filename, file_bytes=file_bytes)
        meeting_id = str(uuid4())
        meeting_title = (title or filename).strip() or "Uploaded Meeting"

        # Guess content type
        content_type = "text/plain"
        lower_name = filename.lower()
        if lower_name.endswith(".docx"):
            content_type = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
        elif lower_name.endswith(".md"):
            content_type = "text/markdown"

        # 1. Store original file in Blob Storage
        storage_key = f"transcripts/{meeting_id}/{filename}"
        try:
            uploaded_key = await self.storage.upload(
                key=storage_key,
                data=file_bytes,
                content_type=content_type
            )
        except Exception as upload_err:
            logger.error(f"[INGESTION ERROR] Blob storage upload failed: {upload_err}", exc_info=True)
            raise RuntimeError(f"Failed to store transcript file in object storage: {upload_err}") from upload_err

        # 2. Persist initial Meeting record
        meeting = Meeting(
            id=meeting_id,
            title=meeting_title,
            source_type="file",
            storage_key=uploaded_key,
            transcript_text=cleaned_text,
            transcript_raw=cleaned_text,
            organization_id=org_id,
            status="completed",
            analysis_status="processing",
            created_at=datetime.utcnow(),
            updated_at=datetime.utcnow()
        )
        db.add(meeting)
        db.commit()
        db.refresh(meeting)

        # 3. Process analysis pipeline
        return await self.analyze_transcript(db=db, meeting_id=meeting.id)

    async def analyze_transcript(
        self,
        db: Session,
        meeting_id: str
    ) -> TranscriptIngestResponse:
        """
        Core shared analysis pipeline:
        1. Retrieves Meeting record
        2. Sends transcript to LLM analysis service
        3. Idempotently cleans up previous extracted records (for re-analysis / retries)
        4. Persists extracted Decisions, Tasks, and Knowledge records
        5. Updates Meeting status to 'completed' (or 'failed' upon error without losing transcript)
        """
        meeting = db.query(Meeting).filter(Meeting.id == meeting_id).first()
        if not meeting:
            raise ValueError(f"Meeting '{meeting_id}' not found.")

        transcript_text = meeting.transcript_text or meeting.transcript_raw
        if not transcript_text or not transcript_text.strip():
            meeting.analysis_status = "failed"
            meeting.error_message = "No transcript content available for analysis."
            db.commit()
            return TranscriptIngestResponse(
                meeting_id=meeting.id,
                status="failed",
                source=meeting.source_type or "pasted",
                decisions_count=0,
                tasks_count=0,
                knowledge_count=0,
                error="No transcript content available for analysis."
            )

        meeting.analysis_status = "processing"
        meeting.error_message = None
        db.commit()

        try:
            analysis_result: TranscriptAnalysisOutput = await self._analyzer.analyze(
                transcript_text=transcript_text,
                title=meeting.title
            )

            # Idempotency: Remove existing extracted records for this meeting
            db.query(Decision).filter(Decision.meeting_id == meeting.id).delete()
            db.query(Task).filter(Task.meeting_id == meeting.id).delete()
            db.query(Knowledge).filter(Knowledge.meeting_id == meeting.id).delete()

            # Persist Decisions
            created_decisions = []
            for d in analysis_result.decisions:
                decision_rec = Decision(
                    meeting_id=meeting.id,
                    decision=d.decision,
                    rationale=d.rationale,
                    participants=d.participants or [],
                    source_reference=d.source_reference.model_dump()
                )
                db.add(decision_rec)
                created_decisions.append(decision_rec)

            # Persist Tasks
            created_tasks = []
            for t in analysis_result.tasks:
                task_rec = Task(
                    meeting_id=meeting.id,
                    title=t.title,
                    description=t.description,
                    owner=t.owner,
                    deadline=t.deadline,
                    status="open",
                    source_reference=t.source_reference.model_dump()
                )
                db.add(task_rec)
                created_tasks.append(task_rec)

            # Persist Knowledge
            created_knowledge = []
            for k in analysis_result.knowledge:
                knowledge_rec = Knowledge(
                    meeting_id=meeting.id,
                    topic=k.topic,
                    content=k.content,
                    source_reference=k.source_reference.model_dump()
                )
                db.add(knowledge_rec)
                created_knowledge.append(knowledge_rec)

            meeting.analysis_status = "completed"
            meeting.analyzed_at = datetime.utcnow()
            meeting.updated_at = datetime.utcnow()
            meeting.error_message = None
            db.commit()

            # Refresh records to get generated IDs
            for rec in created_decisions + created_tasks + created_knowledge:
                db.refresh(rec)

            return TranscriptIngestResponse(
                meeting_id=meeting.id,
                status="completed",
                source=meeting.source_type or "pasted",
                decisions_count=len(created_decisions),
                tasks_count=len(created_tasks),
                knowledge_count=len(created_knowledge),
                decisions=[
                    DecisionResponse(
                        id=d.id,
                        decision=d.decision,
                        rationale=d.rationale,
                        participants=d.participants,
                        source_reference=SourceReferenceResponse(**d.source_reference)
                    )
                    for d in created_decisions
                ],
                tasks=[
                    TaskResponse(
                        id=t.id,
                        title=t.title,
                        description=t.description,
                        owner=t.owner,
                        deadline=t.deadline,
                        status=t.status,
                        source_reference=SourceReferenceResponse(**t.source_reference)
                    )
                    for t in created_tasks
                ],
                knowledge=[
                    KnowledgeResponse(
                        id=k.id,
                        topic=k.topic,
                        content=k.content,
                        source_reference=SourceReferenceResponse(**k.source_reference)
                    )
                    for k in created_knowledge
                ]
            )

        except Exception as err:
            logger.error(f"[INGESTION ANALYSIS ERROR] Failed analyzing meeting '{meeting.id}': {err}", exc_info=True)
            # DO NOT LOSE TRANSCRIPT: Keep meeting record in DB with analysis_status="failed"
            meeting.analysis_status = "failed"
            meeting.error_message = str(err)
            meeting.updated_at = datetime.utcnow()
            try:
                db.commit()
            except Exception:
                db.rollback()

            return TranscriptIngestResponse(
                meeting_id=meeting.id,
                status="failed",
                source=meeting.source_type or "pasted",
                decisions_count=0,
                tasks_count=0,
                knowledge_count=0,
                error=f"Analysis failed: {str(err)}"
            )


transcript_ingestion_service = TranscriptIngestionService()
