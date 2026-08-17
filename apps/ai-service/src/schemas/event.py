from datetime import datetime
from typing import Optional, Any, List
from pydantic import BaseModel, Field


class TranscriptSegment(BaseModel):
    speaker: str
    text: str
    timestamp: Optional[str] = None


class MeetingIngestRequest(BaseModel):
    meeting_id: str
    title: str
    started_at: Optional[str] = None
    participants: List[str] = []
    segments: List[TranscriptSegment]
    repository_id: Optional[str] = "default"


class EventCreate(BaseModel):
    source: str = Field(..., description="e.g. meeting, github, manual")
    event_type: str = Field(..., description="e.g. meeting_commitment, github_pr_created")
    timestamp: Optional[datetime] = None
    actor: Optional[str] = None
    entity_type: Optional[str] = None
    entity_id: Optional[str] = None
    content: str
    reference_id: Optional[str] = None
    metadata_json: Optional[dict[str, Any]] = None


class EventResponse(BaseModel):
    id: str
    source: str
    event_type: str
    timestamp: datetime
    actor: Optional[str] = None
    entity_type: Optional[str] = None
    entity_id: Optional[str] = None
    content: str
    reference_id: Optional[str] = None
    metadata_json: Optional[dict[str, Any]] = None
    created_at: datetime

    class Config:
        from_attributes = True


class EventRelationshipResponse(BaseModel):
    id: str
    source_event_id: str
    target_event_id: str
    relationship_type: str
    confidence_score: float
    created_at: datetime

    class Config:
        from_attributes = True


class EventTimelineItem(BaseModel):
    event: EventResponse
    relationships: List[EventRelationshipResponse] = []
    related_events: List[EventResponse] = []
