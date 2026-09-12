from api.deps import get_current_user
import logging
from typing import Optional, List
from fastapi import (
    APIRouter,
    Depends,
    HTTPException,
    BackgroundTasks,
    status,
    WebSocket,
    WebSocketDisconnect,
    UploadFile,
    File,
    Form
)
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session

from db.session import get_db
from models.meeting import Meeting
from models.user import User
from api.deps import require_auth, optional_auth
from models.decision import Decision
from models.task import Task
from models.knowledge import Knowledge
from schemas.transcript import (
    TranscriptPastedRequest,
    TranscriptIngestResponse,
    MeetingAnalysisDetailResponse,
    DecisionResponse,
    TaskResponse,
    KnowledgeResponse,
    SourceReferenceResponse
)
from services.transcript_ingestion_service import transcript_ingestion_service
from services.transcript_parser import TranscriptParseError
from schemas.meeting import (
    JoinMeetingRequest,
    StartMeetingRequest,
    StartMeetingResponse,
    CanonicalTranscriptResponse,
    TranscriptSegmentResponse,
    MeetingSubmitRequest,
    MeetingAcceptedResponse,
    MeetingDetailResponse
)
from schemas.live_meeting import (
    LiveMeetingWindowRequest,
    LiveMeetingWindowResponse,
    LiveMeetingStateResponse,
    StartLiveSessionRequest,
    LiveSessionControlResponse,
    LiveTranscriptSegment,
    LiveTranscriptBufferResponse,
    LiveChunkIngestRequest
)
from services.meeting_service import meeting_service
from services.live_meeting_service import live_meeting_service
from services.meeting_live_broadcaster import meeting_live_broadcaster
from transcription.exceptions import (
    InvalidMeetingUrlError,
    ConfigurationError,
    ProviderAuthError,
    ProviderUnavailableError,
    ProviderTimeoutError,
    TranscriptionError
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/meetings", tags=["Meetings"])


# =======================================================
# 0. Transcript Ingestion & Analysis Pipelines
# =======================================================

@router.post(
    "/transcript/upload",
    response_model=TranscriptIngestResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Upload and analyze meeting transcript file (.txt, .md, .docx)"
)
async def upload_transcript_file(
    file: UploadFile = File(...),
    title: Optional[str] = Form(None),
    user: Optional[User] = Depends(optional_auth),
    db: Session = Depends(get_db)
):
    """
    Accepts a transcript file (.txt, .md, .docx) via multipart/form-data.
    1. Validates file format and size limits.
    2. Stores the raw file into Supabase/Blob storage.
    3. Normalizes and extracts transcript text.
    4. Runs LLM analysis to extract Decisions, Tasks, and Knowledge with source references.
    5. Persists records to PostgreSQL idempotently.
    6. Returns meeting ID and analysis summary/counts.
    """
    try:
        file_bytes = await file.read()
        org_id = getattr(user, "organization_id", None) if user else None
        user_id = getattr(user, "id", None) if user else None

        result = await transcript_ingestion_service.ingest_file_transcript(
            db=db,
            filename=file.filename or "transcript.txt",
            file_bytes=file_bytes,
            title=title,
            user_id=user_id,
            org_id=org_id
        )
        return result
    except TranscriptParseError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(exc)
        )
    except RuntimeError as exc:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=str(exc)
        )
    except Exception as exc:
        logger.error(f"[API TRANSCRIPT UPLOAD ERROR] {exc}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to ingest transcript file: {str(exc)}"
        )


@router.post(
    "/transcript",
    response_model=TranscriptIngestResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Ingest and analyze pasted meeting transcript text"
)
async def ingest_pasted_transcript(
    request: TranscriptPastedRequest,
    user: User = Depends(require_auth),
    db: Session = Depends(get_db)
):
    """
    Accepts raw transcript text in JSON format.
    1. Validates transcript content is not empty.
    2. Stores original transcript into Supabase/Blob storage.
    3. Creates Meeting record.
    4. Analyzes transcript with dedicated LLM analysis pipeline.
    5. Persists structured Decisions, Tasks, and Knowledge with source references.
    6. Returns meeting ID and analysis summary/counts.
    """
    try:
        org_id = getattr(user, "organization_id", None) if user else None
        user_id = getattr(user, "id", None) if user else None

        result = await transcript_ingestion_service.ingest_pasted_transcript(
            db=db,
            title=request.title,
            transcript_raw=request.transcript,
            user_id=user_id,
            org_id=org_id
        )
        return result
    except TranscriptParseError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(exc)
        )
    except RuntimeError as exc:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=str(exc)
        )
    except Exception as exc:
        logger.error(f"[API PASTED TRANSCRIPT ERROR] {exc}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to ingest pasted transcript: {str(exc)}"
        )


@router.post(
    "/{meeting_id}/reanalyze",
    response_model=TranscriptIngestResponse,
    summary="Re-analyze an existing meeting transcript without duplicate records"
)
async def reanalyze_meeting(
    meeting_id: str,
    db: Session = Depends(get_db)
):
    """
    Safely re-analyzes an existing meeting transcript.
    Cleans up previously extracted decisions, tasks, and knowledge to ensure idempotency.
    """
    try:
        return await transcript_ingestion_service.analyze_transcript(db=db, meeting_id=meeting_id)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc))
    except Exception as exc:
        logger.error(f"[API REANALYZE ERROR] {exc}", exc_info=True)
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(exc))


@router.get(
    "/{meeting_id}/analysis",
    response_model=MeetingAnalysisDetailResponse,
    summary="Retrieve extracted decisions, tasks, and knowledge for a meeting"
)
def get_meeting_analysis(
    meeting_id: str,
    db: Session = Depends(get_db)
):
    """Retrieves all structured decisions, tasks, and knowledge extracted from a meeting transcript."""
    meeting = db.query(Meeting).filter(
        (Meeting.id == meeting_id) | (Meeting.native_meeting_id == meeting_id) | (Meeting.external_meeting_id == meeting_id)
    ).first()
    if not meeting:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Meeting '{meeting_id}' not found.")

    decisions = db.query(Decision).filter(Decision.meeting_id == meeting.id).all()
    tasks = db.query(Task).filter(Task.meeting_id == meeting.id).all()
    knowledge = db.query(Knowledge).filter(Knowledge.meeting_id == meeting.id).all()

    return MeetingAnalysisDetailResponse(
        meeting_id=meeting.id,
        title=meeting.title,
        status=meeting.analysis_status or meeting.status,
        source=meeting.source_type,
        storage_key=meeting.storage_key,
        analyzed_at=meeting.analyzed_at.isoformat() if meeting.analyzed_at else None,
        decisions_count=len(decisions),
        tasks_count=len(tasks),
        knowledge_count=len(knowledge),
        decisions=[
            DecisionResponse(
                id=d.id,
                decision=d.decision,
                rationale=d.rationale,
                participants=d.participants or [],
                source_reference=SourceReferenceResponse(**d.source_reference)
            )
            for d in decisions
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
            for t in tasks
        ],
        knowledge=[
            KnowledgeResponse(
                id=k.id,
                topic=k.topic,
                content=k.content,
                source_reference=SourceReferenceResponse(**k.source_reference)
            )
            for k in knowledge
        ]
    )


# =======================================================
# 1. Start Meeting Transcription (Vexa Integration Flow)
# =======================================================

@router.post(
    "",
    response_model=StartMeetingResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Start meeting transcription with Vexa bot"
)
async def join_meeting(
    request: JoinMeetingRequest,
    user: User = Depends(require_auth),
    db: Session = Depends(get_db)
):
    """
    Accepts a Meet URL, extracts the native meeting ID,
    dispatches a Vexa transcription bot, and creates a local meeting record.
    
    Returns our internal meeting UUID and initial 'joining' status.
    """
    logger.info(f"[API] Starting transcription for meeting URL: {request.meeting_url}")
    try:
        meeting = await meeting_service.start_vexa_meeting(
            db=db,
            meeting_url=request.meeting_url,
            bot_name=request.bot_name,
            user=user
        )

        return StartMeetingResponse(
            id=meeting.id,
            status=meeting.status,
            title=meeting.title,
            platform=meeting.platform,
            meeting_url=meeting.meeting_url,
            native_meeting_id=meeting.native_meeting_id
        )

    except InvalidMeetingUrlError as exc:
        logger.warning(f"[API] Invalid meeting URL: {exc.message}")
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=exc.message
        )
    except ConfigurationError as exc:
        logger.error(f"[API] Transcription configuration error: {exc.message}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Transcription service is not configured properly."
        )
    except ProviderAuthError as exc:
        logger.error(f"[API] Transcription auth error: {exc.message}")
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Failed to authenticate with transcription provider."
        )
    except ProviderUnavailableError as exc:
        logger.error(f"[API] Transcription provider unavailable: {exc.message}")
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Transcription provider is currently unavailable. Please try again later."
        )
    except ProviderTimeoutError as exc:
        logger.error(f"[API] Transcription provider timeout: {exc.message}")
        raise HTTPException(
            status_code=status.HTTP_504_GATEWAY_TIMEOUT,
            detail="Request to transcription provider timed out."
        )
    except TranscriptionError as exc:
        logger.error(f"[API] Transcription error: {exc.message}")
        raise HTTPException(
            status_code=exc.status_code if exc.status_code else status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=exc.message
        )
    except Exception as exc:
        logger.error(f"[API] Unexpected error starting meeting: {exc}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to start meeting transcription."
        )


# =======================================================
# 2. Retrieve Canonical Transcript
# =======================================================

@router.get(
    "/{meeting_id}/transcript",
    response_model=CanonicalTranscriptResponse,
    summary="Retrieve canonical transcript for a meeting"
)
async def get_meeting_transcript(
    meeting_id: str,
    db: Session = Depends(get_db)
):
    """
    Fetches the latest transcript from the transcription provider for the given meeting ID,
    normalizes it into our canonical transcript format, and returns it to the client.
    """
    logger.info(f"[API] Transcript requested for meeting_id='{meeting_id}'")
    try:
        transcript = await meeting_service.get_canonical_transcript(
            db=db,
            meeting_id=meeting_id
        )

        if not transcript:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Meeting with id '{meeting_id}' not found."
            )

        return CanonicalTranscriptResponse(
            meeting_id=transcript.meeting_id,
            title=transcript.title,
            platform=transcript.platform,
            participants=transcript.participants,
            segments=[
                TranscriptSegmentResponse(
                    speaker=s.speaker,
                    text=s.text,
                    start_time=s.start_time,
                    end_time=s.end_time
                )
                for s in transcript.segments
            ],
            status=transcript.status
        )

    except HTTPException:
        raise
    except ProviderAuthError as exc:
        logger.error(f"[API] Provider auth failure: {exc.message}")
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Failed to authenticate with transcription provider."
        )
    except ProviderUnavailableError as exc:
        logger.error(f"[API] Provider unavailable: {exc.message}")
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Transcription provider is currently unavailable."
        )
    except ProviderTimeoutError as exc:
        logger.error(f"[API] Provider timeout: {exc.message}")
        raise HTTPException(
            status_code=status.HTTP_504_GATEWAY_TIMEOUT,
            detail="Request to transcription provider timed out."
        )
    except Exception as exc:
        logger.error(f"[API] Unexpected error retrieving transcript: {exc}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to retrieve meeting transcript."
        )


# =======================================================
# 3. Meeting Details & List Queries
# =======================================================

@router.get(
    "/{meeting_id}",
    response_model=MeetingDetailResponse,
    summary="Get meeting details, status, decisions, and action items"
)
def get_meeting(
    meeting_id: str,
    db: Session = Depends(get_db)
):
    """Retrieves full details for a meeting."""
    meeting = db.query(Meeting).filter(
        (Meeting.id == meeting_id) | (Meeting.native_meeting_id == meeting_id) | (Meeting.external_meeting_id == meeting_id)
    ).first()

    if not meeting:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Meeting with id '{meeting_id}' not found."
        )

    return MeetingDetailResponse(
        id=meeting.id,
        external_meeting_id=meeting.external_meeting_id,
        title=meeting.title,
        status=meeting.status,
        platform=meeting.platform or "google_meet",
        meeting_url=meeting.meeting_url,
        native_meeting_id=meeting.native_meeting_id,
        vexa_meeting_id=meeting.vexa_meeting_id,
        error_message=meeting.error_message,
        started_at=meeting.started_at,
        ended_at=meeting.ended_at,
        participants=meeting.participants_json or [],
        summary=meeting.summary,
        decisions=meeting.decisions_json or [],
        action_items=meeting.action_items_json or [],
        facts_count=meeting.facts_count or 0,
        metadata=meeting.metadata_json or {},
        created_at=meeting.created_at,
        updated_at=meeting.updated_at
    )


@router.get(
    "",
    response_model=List[MeetingDetailResponse],
    summary="List meetings with status and details"
)
def list_meetings(
    status_filter: Optional[str] = None,
    limit: int = 50,
    db: Session = Depends(get_db)
):
    """List meetings with optional filtering by status (joining, active, completed, failed)."""
    query = db.query(Meeting)
    if status_filter:
        query = query.filter(Meeting.status == status_filter.lower())
    
    meetings = query.order_by(Meeting.created_at.desc()).limit(limit).all()

    return [
        MeetingDetailResponse(
            id=m.id,
            external_meeting_id=m.external_meeting_id,
            title=m.title,
            status=m.status,
            platform=m.platform or "google_meet",
            meeting_url=m.meeting_url,
            native_meeting_id=m.native_meeting_id,
            vexa_meeting_id=m.vexa_meeting_id,
            error_message=m.error_message,
            started_at=m.started_at,
            ended_at=m.ended_at,
            participants=m.participants_json or [],
            summary=m.summary,
            decisions=m.decisions_json or [],
            action_items=m.action_items_json or [],
            facts_count=m.facts_count or 0,
            metadata=m.metadata_json or {},
            created_at=m.created_at,
            updated_at=m.updated_at
        )
        for m in meetings
    ]


# =======================================================
# 4. Legacy AI Ingestion & Analysis Pipelines
# =======================================================

@router.post(
    "",
    response_model=MeetingAcceptedResponse,
    status_code=status.HTTP_202_ACCEPTED,
    summary="Submit meeting transcript for background AI processing"
)
async def submit_meeting(
    request: MeetingSubmitRequest,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db)
):
    """Accepts raw meeting transcript / segments and queues background AI intelligence extraction."""
    try:
        meeting = meeting_service.create_meeting_job(db=db, request=request)
        background_tasks.add_task(meeting_service.process_meeting_job, meeting.id)

        return MeetingAcceptedResponse(
            status="accepted",
            meeting_id=meeting.id,
            external_meeting_id=meeting.external_meeting_id,
            message="Meeting accepted and queued for background processing.",
            created_at=meeting.created_at
        )
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to accept meeting: {str(e)}"
        )


# =======================================================
# 5. Live Meeting Intelligence (LangChain & LangGraph)
# =======================================================

@router.post(
    "/{meeting_id}/live-window",
    response_model=LiveMeetingWindowResponse,
    summary="Process a 4-minute live meeting transcript window using LangGraph"
)
async def process_live_window(
    meeting_id: str,
    request: Optional[LiveMeetingWindowRequest] = None,
    db: Session = Depends(get_db)
):
    """
    Processes an incremental 4-minute meeting transcript window.
    Maintains the 4 context elements:
      1. Periodic 4-minute cadence
      2. Previous minutes summary
      3. Conditional RAG context
      4. Meeting state (members, recent decisions, tasks)
    """
    try:
        response = await live_meeting_service.process_window(
            db=db,
            meeting_id=meeting_id,
            request=request
        )
        return response
    except ValueError as val_err:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(val_err)
        )
    except Exception as exc:
        logger.error(f"[API LIVE WINDOW ERROR] {exc}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to process live meeting window: {str(exc)}"
        )


@router.get(
    "/{meeting_id}/live-state",
    response_model=LiveMeetingStateResponse,
    summary="Retrieve current real-time meeting state and rolling summary"
)
def get_live_meeting_state(
    meeting_id: str,
    db: Session = Depends(get_db)
):
    """
    Retrieves the real-time meeting state:
    - Active members / speakers
    - Accumulated rolling summary
    - Confirmed decisions
    - Action items / tasks
    - Window processing metrics
    """
    try:
        return live_meeting_service.get_live_state(db=db, meeting_id=meeting_id)
    except ValueError as val_err:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(val_err)
        )
    except Exception as exc:
        logger.error(f"[API LIVE STATE ERROR] {exc}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to retrieve live meeting state: {str(exc)}"
        )


@router.post(
    "/{meeting_id}/live/start",
    response_model=LiveSessionControlResponse,
    summary="Start background 4-minute polling loop for active meeting"
)
def start_live_session(
    meeting_id: str,
    request: Optional[StartLiveSessionRequest] = None,
    db: Session = Depends(get_db)
):
    """Starts background live runner that invokes LangGraph every 4 minutes."""
    meeting = db.query(Meeting).filter(
        (Meeting.id == meeting_id) | (Meeting.native_meeting_id == meeting_id) | (Meeting.external_meeting_id == meeting_id)
    ).first()

    if not meeting:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Meeting with id '{meeting_id}' not found."
        )

    interval = request.interval_seconds if request and request.interval_seconds else 240
    live_meeting_service.start_live_session(meeting_id=meeting.id, interval_seconds=interval)

    return LiveSessionControlResponse(
        meeting_id=meeting.id,
        status="running",
        message=f"Live 4-minute runner started with interval {interval}s."
    )


@router.post(
    "/{meeting_id}/live/stop",
    response_model=LiveSessionControlResponse,
    summary="Stop background 4-minute polling loop for meeting"
)
def stop_live_session(
    meeting_id: str,
    db: Session = Depends(get_db)
):
    """Stops the live background polling runner."""
    meeting = db.query(Meeting).filter(
        (Meeting.id == meeting_id) | (Meeting.native_meeting_id == meeting_id) | (Meeting.external_meeting_id == meeting_id)
    ).first()

    if not meeting:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Meeting with id '{meeting_id}' not found."
        )

    stopped = live_meeting_service.stop_live_session(meeting_id=meeting.id)

    return LiveSessionControlResponse(
        meeting_id=meeting.id,
        status="stopped" if stopped else "not_running",
        message="Live meeting runner stopped." if stopped else "No active live runner was running for this meeting."
    )


# =======================================================
# 6. Real-Time Transcript Streaming (Frontend & Vexa Gateway)
# =======================================================

@router.websocket("/{meeting_id}/live/ws")
async def live_transcript_websocket(
    websocket: WebSocket,
    meeting_id: str,
    db: Session = Depends(get_db)
):
    """
    Bidirectional WebSocket connection for live transcript streaming to frontend clients.
    - Sends connection confirmation & instant catch-up of accumulated segments.
    - Streams live draft utterances ('completed': false) and confirmed speech ('completed': true).
    - Supports client ping/pong keepalives and request_history.
    """
    resolved_id = meeting_id
    meeting = db.query(Meeting).filter(
        (Meeting.id == meeting_id) | (Meeting.native_meeting_id == meeting_id) | (Meeting.external_meeting_id == meeting_id)
    ).first()

    if meeting:
        resolved_id = meeting.id
        if meeting.native_meeting_id:
            meeting_live_broadcaster.register_meeting_mapping(
                meeting_id=meeting.id,
                native_meeting_id=meeting.native_meeting_id
            )
            if meeting.status in ("active", "joining"):
                asyncio.create_task(
                    meeting_live_broadcaster.ensure_upstream_subscribed(
                        native_meeting_id=meeting.native_meeting_id,
                        meeting_id=meeting.id,
                        platform=meeting.platform or "google_meet"
                    )
                )

    await meeting_live_broadcaster.connect_websocket(meeting_id=resolved_id, websocket=websocket)
    try:
        while True:
            data = await websocket.receive_json()
            msg_type = data.get("type") if isinstance(data, dict) else None

            if msg_type == "ping":
                await websocket.send_json({"type": "pong"})
            elif msg_type == "request_history":
                snapshot = meeting_live_broadcaster.get_live_buffer(resolved_id)
                await websocket.send_json({
                    "type": "catch_up",
                    "meeting_id": resolved_id,
                    "segments": [s.model_dump(by_alias=True) for s in snapshot.segments]
                })
    except WebSocketDisconnect:
        meeting_live_broadcaster.disconnect_websocket(meeting_id=resolved_id, websocket=websocket)
    except Exception as exc:
        logger.debug(f"[WS CLIENT ERROR] meeting='{resolved_id}': {exc}")
        meeting_live_broadcaster.disconnect_websocket(meeting_id=resolved_id, websocket=websocket)


@router.get(
    "/{meeting_id}/live/stream",
    summary="Stream live transcript events via Server-Sent Events (SSE)"
)
async def stream_live_transcript_sse(
    meeting_id: str,
    db: Session = Depends(get_db)
):
    """
    Server-Sent Events (SSE) endpoint providing unidirectional streaming of live transcripts.
    Ideal for simple browser EventSource clients in React/Next.js.
    """
    meeting = db.query(Meeting).filter(
        (Meeting.id == meeting_id) | (Meeting.native_meeting_id == meeting_id) | (Meeting.external_meeting_id == meeting_id)
    ).first()

    resolved_id = meeting.id if meeting else meeting_id
    if meeting and meeting.native_meeting_id:
        meeting_live_broadcaster.register_meeting_mapping(
            meeting_id=meeting.id,
            native_meeting_id=meeting.native_meeting_id
        )
        if meeting.status in ("active", "joining"):
            asyncio.create_task(
                meeting_live_broadcaster.ensure_upstream_subscribed(
                    native_meeting_id=meeting.native_meeting_id,
                    meeting_id=meeting.id,
                    platform=meeting.platform or "google_meet"
                )
            )

    return StreamingResponse(
        meeting_live_broadcaster.subscribe_sse(meeting_id=resolved_id),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no"
        }
    )


@router.get(
    "/{meeting_id}/live/transcript",
    response_model=LiveTranscriptBufferResponse,
    summary="Snapshot of currently accumulated live transcript segments"
)
def get_live_transcript_buffer(
    meeting_id: str,
    db: Session = Depends(get_db)
):
    """Retrieves current in-memory buffer of live segments, active speakers, and streaming status."""
    meeting = db.query(Meeting).filter(
        (Meeting.id == meeting_id) | (Meeting.native_meeting_id == meeting_id) | (Meeting.external_meeting_id == meeting_id)
    ).first()

    resolved_id = meeting.id if meeting else meeting_id
    status_str = meeting.status if meeting else "unknown"

    return meeting_live_broadcaster.get_live_buffer(meeting_id=resolved_id, status=status_str)


@router.post(
    "/{meeting_id}/live/transcript-chunk",
    response_model=LiveTranscriptSegment,
    status_code=status.HTTP_201_CREATED,
    summary="Ingest a live transcript chunk directly into real-time broadcaster"
)
async def ingest_transcript_chunk(
    meeting_id: str,
    request: LiveChunkIngestRequest,
    db: Session = Depends(get_db)
):
    """
    Ingests an individual transcript chunk and broadcasts it instantly to all connected frontend clients.
    Useful for external bot bridges, simulated feeds, and webhook integrations.
    """
    meeting = db.query(Meeting).filter(
        (Meeting.id == meeting_id) | (Meeting.native_meeting_id == meeting_id) | (Meeting.external_meeting_id == meeting_id)
    ).first()

    resolved_id = meeting.id if meeting else meeting_id
    segment = await meeting_live_broadcaster.ingest_chunk(meeting_id=resolved_id, request=request)
    return segment

