from datetime import datetime
from typing import Optional, List, Dict, Any, Union
from pydantic import BaseModel, Field, ConfigDict


# ==========================================
# 1. Direct Vexa / Start Meeting Flow Models
# ==========================================

class JoinMeetingRequest(BaseModel):
    """Request to join a Meet meeting and start transcription."""
    meeting_url: str = Field(..., alias="meeting_url", description="Meet URL")
    bot_name: str = Field(..., alias="bot_name", description="Bot name")
    

class StartMeetingRequest(BaseModel):
    """Payload to start meeting transcription by dispatching a bot."""
    meeting_url: str = Field(..., alias="meetingUrl", description="Google Meet URL")
    title: Optional[str] = Field("Engineering Sync", description="Meeting title")

    model_config = ConfigDict(
        populate_by_name=True,
        from_attributes=True
    )


class StartMeetingResponse(BaseModel):
    """Response returned upon dispatching meeting transcription bot."""
    id: str = Field(..., description="Our internal meeting UUID")
    status: str = Field("joining", description="Meeting transcription status: joining, active, completed, failed")
    title: Optional[str] = Field(None, description="Meeting title")
    platform: Optional[str] = Field("google_meet", description="Meeting platform")
    meeting_url: Optional[str] = Field(None, alias="meetingUrl", description="Original meeting URL")
    native_meeting_id: Optional[str] = Field(None, alias="nativeMeetingId", description="Native meeting ID")

    model_config = ConfigDict(
        populate_by_name=True,
        from_attributes=True,
        serialize_by_alias=True
    )


class TranscriptSegmentResponse(BaseModel):
    """Canonical transcript segment returned in API response."""
    speaker: str = Field(..., description="Speaker name or attribution")
    text: str = Field(..., description="Dialogue text")
    start_time: Optional[float] = Field(None, alias="startTime", serialization_alias="startTime", description="Start time in seconds")
    end_time: Optional[float] = Field(None, alias="endTime", serialization_alias="endTime", description="End time in seconds")

    model_config = ConfigDict(
        populate_by_name=True,
        from_attributes=True,
        serialize_by_alias=True
    )


class CanonicalTranscriptResponse(BaseModel):
    """Canonical transcript format returned by GET /meetings/:id/transcript."""
    meeting_id: str = Field(..., alias="meetingId", serialization_alias="meetingId", description="Meeting UUID")
    title: str = Field("Engineering Sync", description="Meeting title")
    platform: str = Field("google_meet", description="Meeting platform")
    participants: List[str] = Field(default_factory=list, description="List of participant names")
    segments: List[TranscriptSegmentResponse] = Field(default_factory=list, description="Ordered transcript segments")
    status: Optional[str] = Field(None, description="Current meeting status")

    model_config = ConfigDict(
        populate_by_name=True,
        from_attributes=True,
        serialize_by_alias=True
    )


# ==========================================
# 2. Existing AI Ingestion / Pipeline Models
# ==========================================

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
    status: str  # joining, active, completed, failed, PROCESSING
    platform: Optional[str] = "google_meet"
    meeting_url: Optional[str] = None
    native_meeting_id: Optional[str] = None
    vexa_meeting_id: Optional[str] = None
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

    model_config = ConfigDict(
        from_attributes=True,
        populate_by_name=True
    )
