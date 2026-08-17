from typing import Optional, List
from fastapi import APIRouter, Depends, HTTPException, BackgroundTasks, status
from sqlalchemy.orm import Session

from src.db.session import get_db
from src.models.meeting import Meeting
from src.schemas.meeting import (
    MeetingSubmitRequest,
    MeetingAcceptedResponse,
    MeetingDetailResponse
)
from src.services.meeting_service import meeting_service

router = APIRouter(prefix="/api/v1/meetings", tags=["Meetings"])


@router.post(
    "",
    response_model=MeetingAcceptedResponse,
    status_code=status.HTTP_202_ACCEPTED,
    summary="Submit meeting transcript for asynchronous processing"
)
@router.post(
    "/process",
    response_model=MeetingAcceptedResponse,
    status_code=status.HTTP_202_ACCEPTED,
    summary="Submit meeting transcript for asynchronous processing (alias)"
)
async def submit_meeting(
    request: MeetingSubmitRequest,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db)
):
    """
    Accepts meeting transcript / segments and queues background AI intelligence extraction.
    
    Returns HTTP 202 Accepted immediately with meeting ID and status.
    
    Background Processing Steps:
    - Resolves speaker identities to canonical Persons
    - Extracts executive & engineering summary
    - Extracts architectural/technical decisions
    - Extracts actionable commitments and creates tracked WorkItems
    - Creates discrete searchable Fact units with 1536-dim vector embeddings
    - Indexes into Timeline events and pgvector RAG
    """
    try:
        # 1. Create meeting record in DB with PROCESSING status
        meeting = meeting_service.create_meeting_job(db=db, request=request)

        # 2. Enqueue asynchronous background processing task
        background_tasks.add_task(meeting_service.process_meeting_job, meeting.id)

        # 3. Return HTTP 202 Accepted
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


@router.get(
    "/{meeting_id}",
    response_model=MeetingDetailResponse,
    summary="Get meeting details, status, decisions, and action items"
)
def get_meeting(
    meeting_id: str,
    db: Session = Depends(get_db)
):
    """Retrieves full details for a meeting including AI summary, decisions, and action items."""
    meeting = db.query(Meeting).filter(
        (Meeting.id == meeting_id) | (Meeting.external_meeting_id == meeting_id)
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
    summary="List meetings with status and extracted intelligence"
)
def list_meetings(
    status_filter: Optional[str] = None,
    limit: int = 50,
    db: Session = Depends(get_db)
):
    """List meetings with optional filtering by status (PROCESSING, COMPLETED, FAILED)."""
    query = db.query(Meeting)
    if status_filter:
        query = query.filter(Meeting.status == status_filter.upper())
    
    meetings = query.order_by(Meeting.created_at.desc()).limit(limit).all()

    return [
        MeetingDetailResponse(
            id=m.id,
            external_meeting_id=m.external_meeting_id,
            title=m.title,
            status=m.status,
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
