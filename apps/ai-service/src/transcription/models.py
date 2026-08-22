from typing import List, Optional
from pydantic import BaseModel, Field, ConfigDict


class CanonicalTranscriptSegment(BaseModel):
    """Normalized transcript segment with speaker attribution and timing."""
    speaker: str = Field(..., description="Name or label of the speaker")
    text: str = Field(..., description="Spoken text in this segment")
    start_time: Optional[float] = Field(None, alias="startTime", serialization_alias="startTime", description="Start time in seconds")
    end_time: Optional[float] = Field(None, alias="endTime", serialization_alias="endTime", description="End time in seconds")

    model_config = ConfigDict(
        populate_by_name=True,
        serialize_by_alias=True
    )


class CanonicalTranscript(BaseModel):
    """Canonical representation of a meeting transcript."""
    meeting_id: str = Field(..., alias="meetingId", serialization_alias="meetingId", description="Internal meeting UUID")
    title: str = Field("Engineering Sync", description="Meeting title")
    platform: str = Field("google_meet", description="Platform e.g. google_meet")
    participants: List[str] = Field(default_factory=list, description="List of participant names detected")
    segments: List[CanonicalTranscriptSegment] = Field(default_factory=list, description="Ordered transcript segments")
    status: Optional[str] = Field(None, description="Current meeting/transcription status")

    model_config = ConfigDict(
        populate_by_name=True,
        serialize_by_alias=True
    )
