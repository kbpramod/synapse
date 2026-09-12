from typing import List, Optional
from pydantic import BaseModel, Field


class TranscriptPastedRequest(BaseModel):
    title: Optional[str] = Field(default="Untitled Meeting", description="Title of the meeting")
    transcript: str = Field(..., min_length=1, description="Raw transcript text content")


class SourceReferenceResponse(BaseModel):
    text: str = Field(description="Direct quotation or reference from transcript")
    speaker: Optional[str] = Field(default=None, description="Speaker name or null")
    timestamp: Optional[str] = Field(default=None, description="Timestamp if preserved or null")


class DecisionResponse(BaseModel):
    id: str
    decision: str
    rationale: Optional[str] = None
    participants: List[str] = Field(default_factory=list)
    source_reference: SourceReferenceResponse


class TaskResponse(BaseModel):
    id: str
    title: str
    description: Optional[str] = None
    owner: Optional[str] = None
    deadline: Optional[str] = None
    status: str = "open"
    source_reference: SourceReferenceResponse


class KnowledgeResponse(BaseModel):
    id: str
    topic: str
    content: str
    source_reference: SourceReferenceResponse


class TranscriptIngestResponse(BaseModel):
    meeting_id: str
    status: str = Field(description="Analysis status: completed, failed, processing")
    source: str = Field(description="Ingestion source type: file or pasted")
    decisions_count: int = 0
    tasks_count: int = 0
    knowledge_count: int = 0
    decisions: Optional[List[DecisionResponse]] = None
    tasks: Optional[List[TaskResponse]] = None
    knowledge: Optional[List[KnowledgeResponse]] = None
    error: Optional[str] = None


class MeetingAnalysisDetailResponse(BaseModel):
    meeting_id: str
    title: str
    status: str
    source: Optional[str] = None
    storage_key: Optional[str] = None
    analyzed_at: Optional[str] = None
    decisions_count: int
    tasks_count: int
    knowledge_count: int
    decisions: List[DecisionResponse]
    tasks: List[TaskResponse]
    knowledge: List[KnowledgeResponse]
