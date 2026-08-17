from typing import Optional, List
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from src.db.session import get_db
from src.schemas.event import (
    MeetingIngestRequest,
    EventCreate,
    EventResponse,
    EventTimelineItem
)
from src.services.event_service import event_service
from src.repositories.event_repository import event_repository

router = APIRouter(prefix="/api/v1/events", tags=["Events"])


@router.post("/meeting", response_model=List[EventResponse], status_code=status.HTTP_201_CREATED)
async def ingest_meeting(
    request: MeetingIngestRequest,
    db: Session = Depends(get_db)
):
    """Ingest a meeting transcript, extract commitments/decisions via LLM, and index events."""
    try:
        events = await event_service.ingest_meeting(db, request)
        return events
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to ingest meeting transcript: {str(e)}"
        )


@router.post("", response_model=EventResponse, status_code=status.HTTP_201_CREATED)
async def ingest_event(
    event_in: EventCreate,
    db: Session = Depends(get_db)
):
    """Idempotently ingest a generic event (e.g. GitHub PR, deployment, manual log)."""
    try:
        event, _ = await event_service.ingest_event(db, event_in)
        return event
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to ingest event: {str(e)}"
        )


@router.get("", response_model=List[EventResponse])
def list_events(
    source: Optional[str] = None,
    actor: Optional[str] = None,
    entity_id: Optional[str] = None,
    limit: int = 50,
    db: Session = Depends(get_db)
):
    """List events with optional filtering by source, actor, or entity_id."""
    return event_repository.list_events(
        db=db,
        source=source,
        actor=actor,
        entity_id=entity_id,
        limit=limit
    )


@router.get("/timeline", response_model=List[EventTimelineItem])
def get_timeline(
    entity_id: Optional[str] = None,
    actor: Optional[str] = None,
    limit: int = 50,
    db: Session = Depends(get_db)
):
    """Retrieve event timeline with linked cross-source correlations."""
    return event_service.get_timeline(
        db=db,
        entity_id=entity_id,
        actor=actor,
        limit=limit
    )
