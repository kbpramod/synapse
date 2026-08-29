import logging
from typing import Optional, List
from fastapi import APIRouter, Depends, HTTPException, BackgroundTasks, status
from sqlalchemy.orm import Session

from db.session import get_db
from models.meeting import Meeting
from models.user import User
from api.deps import require_auth
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
from services.meeting_service import meeting_service
from transcription.exceptions import (
    InvalidMeetingUrlError,
    ConfigurationError,
    ProviderAuthError,
    ProviderUnavailableError,
    ProviderTimeoutError,
    TranscriptionError
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/meetings", tags=["Meetings"])
v1_router = APIRouter(prefix="/api/v1/meetings", tags=["Meetings"])


# =======================================================
# 1. Start Meeting Transcription (Vexa Integration Flow)
# =======================================================

@router.post(
    "",
    response_model=StartMeetingResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Start meeting transcription with Vexa bot"
)
@v1_router.post(
    "/join",
    response_model=StartMeetingResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Start meeting transcription with Vexa bot (alias)"
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
@v1_router.get(
    "/{meeting_id}/transcript",
    response_model=CanonicalTranscriptResponse,
    summary="Retrieve canonical transcript for a meeting (alias)"
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
@v1_router.get(
    "/{meeting_id}",
    response_model=MeetingDetailResponse,
    summary="Get meeting details (alias)"
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
@v1_router.get(
    "",
    response_model=List[MeetingDetailResponse],
    summary="List meetings (alias)"
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

@v1_router.post(
    "",
    response_model=MeetingAcceptedResponse,
    status_code=status.HTTP_202_ACCEPTED,
    summary="Submit meeting transcript for background AI processing"
)
@v1_router.post(
    "/process",
    response_model=MeetingAcceptedResponse,
    status_code=status.HTTP_202_ACCEPTED,
    summary="Submit meeting transcript for background AI processing (alias)"
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
