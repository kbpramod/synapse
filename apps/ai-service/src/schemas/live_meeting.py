from datetime import datetime
from typing import Optional, List, Dict, Any, Union
from pydantic import BaseModel, Field, ConfigDict
from schemas.meeting import MeetingSegment


class LiveDecisionItem(BaseModel):
    decision: str = Field(..., description="The agreed decision or architectural consensus")
    topic: Optional[str] = Field("Architecture", description="Topic or category of the decision")
    rationale: Optional[str] = Field(None, description="Why this decision was reached")
    timestamp: Optional[str] = Field(None, description="Timestamp or window mark of decision")

    model_config = ConfigDict(populate_by_name=True, from_attributes=True)


class LiveTaskItem(BaseModel):
    task: str = Field(..., description="Action item or commitment description")
    assignee: Optional[str] = Field("Unassigned", description="Name of assigned team member")
    work_status: Optional[str] = Field("PLANNED", description="Status: PLANNED, IN_PROGRESS, etc.")
    area: Optional[str] = Field("General", description="Component or area name")

    model_config = ConfigDict(populate_by_name=True, from_attributes=True)


class LiveMeetingWindowRequest(BaseModel):
    """Request payload to manually or periodically process a 4-minute live transcript window."""
    segments: Optional[List[MeetingSegment]] = Field(default_factory=list, description="Transcript segments in this 4-minute window")
    transcript: Optional[str] = Field(None, description="Raw transcript text for this window if segments not provided")
    window_start_sec: Optional[float] = Field(None, description="Start timestamp of this slice in seconds")
    window_end_sec: Optional[float] = Field(None, description="End timestamp of this slice in seconds")
    force_rag: Optional[bool] = Field(False, description="Explicitly force RAG retrieval regardless of heuristic/classifier")

    model_config = ConfigDict(populate_by_name=True, from_attributes=True)


class LiveMeetingWindowResponse(BaseModel):
    """Response returned after processing a 4-minute window."""
    meeting_id: str = Field(..., description="Meeting UUID")
    window_index: int = Field(..., description="Index of this 4-minute window (1, 2, ...)")
    window_start_sec: Optional[float] = Field(None, description="Start time in seconds")
    window_end_sec: Optional[float] = Field(None, description="End time in seconds")
    window_summary: str = Field(..., description="Executive summary of this specific 4-minute window")
    accumulated_summary: str = Field(..., description="Rolling consolidated summary up to this window")
    members: List[str] = Field(default_factory=list, description="All active participants detected in meet")
    recent_decisions: List[LiveDecisionItem] = Field(default_factory=list, description="All confirmed decisions up to now")
    tasks: List[LiveTaskItem] = Field(default_factory=list, description="All action items / tasks identified")
    rag_used: bool = Field(False, description="Whether RAG knowledge base was queried during this window")
    rag_context_count: int = Field(0, description="Number of external knowledge facts retrieved")

    model_config = ConfigDict(populate_by_name=True, from_attributes=True)


class LiveMeetingStateResponse(BaseModel):
    """Real-time live meeting state returned for client dashboards."""
    meeting_id: str = Field(..., description="Meeting UUID")
    title: str = Field("Engineering Meeting", description="Meeting title")
    status: str = Field(..., description="Current meeting status (active, completed, joining)")
    members: List[str] = Field(default_factory=list, description="List of participants/speakers")
    summary: Optional[str] = Field(None, description="Latest rolling summary")
    recent_decisions: List[LiveDecisionItem] = Field(default_factory=list, description="Active decisions")
    tasks: List[LiveTaskItem] = Field(default_factory=list, description="Active tasks / action items")
    total_windows_processed: int = Field(0, description="Total 4-minute windows analyzed")
    last_window_time: Optional[float] = Field(None, description="Last processed window end timestamp")
    is_live_active: bool = Field(False, description="Whether the background 4-minute runner is active")

    model_config = ConfigDict(populate_by_name=True, from_attributes=True)


class StartLiveSessionRequest(BaseModel):
    """Configuration to start background live meeting orchestration."""
    interval_seconds: Optional[int] = Field(240, description="Polling / window cadence in seconds (defaults to 240s = 4 min)")

    model_config = ConfigDict(populate_by_name=True, from_attributes=True)


class LiveSessionControlResponse(BaseModel):
    meeting_id: str
    status: str
    message: str

    model_config = ConfigDict(populate_by_name=True, from_attributes=True)


# ==========================================
# Real-Time Transcript Streaming Schemas
# ==========================================

class LiveTranscriptSegment(BaseModel):
    """Real-time transcript segment streamed to frontend."""
    id: str = Field(..., description="Unique segment identifier")
    speaker: str = Field("Unknown", description="Speaker name")
    text: str = Field(..., description="Transcript text")
    completed: bool = Field(False, description="True if finalized, False if live draft")
    start_time: Optional[float] = Field(None, alias="startTime", description="Start time in seconds")
    end_time: Optional[float] = Field(None, alias="endTime", description="End time in seconds")
    timestamp: Optional[str] = Field(None, description="Formatted clock time e.g. 02:45")

    model_config = ConfigDict(
        populate_by_name=True,
        from_attributes=True,
        serialize_by_alias=True
    )


class LiveTranscriptBufferResponse(BaseModel):
    """Snapshot of accumulated live segments and streaming metadata."""
    meeting_id: str = Field(..., description="Meeting UUID")
    status: str = Field(..., description="Meeting status: active, joining, completed")
    segments: List[LiveTranscriptSegment] = Field(default_factory=list, description="Ordered live segments")
    speakers: List[str] = Field(default_factory=list, description="List of detected speakers")
    total_segments: int = Field(0, description="Total segments count")
    is_live: bool = Field(False, description="Whether live transcription is active")

    model_config = ConfigDict(populate_by_name=True, from_attributes=True)


class LiveChunkIngestRequest(BaseModel):
    """Request payload to manually ingest or mock a transcript chunk."""
    id: Optional[str] = Field(None, description="Optional custom segment id")
    speaker: str = Field("Speaker", description="Speaker name")
    text: str = Field(..., description="Transcript text")
    completed: bool = Field(True, description="Whether chunk is finalized")
    start_time: Optional[float] = Field(None, alias="startTime", description="Start time in seconds")
    end_time: Optional[float] = Field(None, alias="endTime", description="End time in seconds")

    model_config = ConfigDict(
        populate_by_name=True,
        from_attributes=True,
        serialize_by_alias=True
    )
