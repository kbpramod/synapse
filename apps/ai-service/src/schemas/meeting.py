from datetime import datetime
from typing import Optional, List, Dict, Any, Union
from pydantic import BaseModel, Field


class MeetingSegment(BaseModel):
    speaker: str = Field(..., description="Name or identifier of the speaker")
    text: str = Field(..., description="Spoken dialogue or text content")
    timestamp: Optional[str] = Field(None, description="Timestamp e.g. '04:15' or ISO string")


class MeetingSubmitRequest(BaseModel):
    meeting_id: Optional[str] = Field(None, description="External or custom meeting identifier. Auto-generated if omitted.")
    title: Optional[str] = Field(None, description="Meeting title or subject")
    started_at: Optional[Union[datetime, str]] = Field(None, description="When the meeting started")
    ended_at: Optional[Union[datetime, str]] = Field(None, description="When the meeting ended")
    participants: Optional[List[Union[str, Dict[str, Any]]]] = Field(default_factory=list, description="List of participant names or metadata dicts")
    transcript: Optional[str] = Field(None, description="Full raw transcript text (if not providing segments)")
    segments: Optional[List[MeetingSegment]] = Field(default_factory=list, description="Structured transcript segments by speaker")
    repository_id: Optional[str] = Field(None, description="Optional GitHub repository UUID or name to associate knowledge with")
    organization_id: Optional[str] = Field(None, description="Optional organization ID")
    metadata: Optional[Dict[str, Any]] = Field(default_factory=dict, description="Additional custom metadata")


class MeetingAcceptedResponse(BaseModel):
    status: str = Field("accepted", description="Processing status, defaults to 'accepted'")
    meeting_id: str = Field(..., description="UUID identifier of the meeting")
    external_meeting_id: Optional[str] = Field(None, description="External meeting ID if provided")
    message: str = Field("Meeting transcript accepted for background processing.", description="Status message")
    created_at: datetime = Field(default_factory=datetime.utcnow, description="Submission timestamp")


class MeetingDecision(BaseModel):
    topic: str
    decision: str
    rationale: Optional[str] = None
    section: Optional[str] = "Architecture"


class MeetingActionItem(BaseModel):
    assignee: str
    task: str
    area: Optional[str] = "General"
    work_status: Optional[str] = "PROPOSED"


class MeetingDetailResponse(BaseModel):
    id: str
    external_meeting_id: Optional[str] = None
    title: str
    status: str  # PROCESSING, COMPLETED, FAILED
    error_message: Optional[str] = None
    started_at: Optional[datetime] = None
    ended_at: Optional[datetime] = None
    participants: List[Any] = []
    summary: Optional[str] = None
    decisions: List[Dict[str, Any]] = []
    action_items: List[Dict[str, Any]] = []
    facts_count: int = 0
    metadata: Dict[str, Any] = {}
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True
