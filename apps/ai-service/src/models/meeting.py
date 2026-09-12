from datetime import datetime
from uuid import uuid4
from sqlalchemy import Column, String, Text, DateTime, JSON, Integer, ForeignKey
from sqlalchemy.orm import relationship

from src.db.database import Base


class Meeting(Base):
    __tablename__ = "meetings"
    __table_args__ = {'extend_existing': True}

    id = Column(String, primary_key=True, default=lambda: str(uuid4()))
    external_meeting_id = Column(String, index=True, nullable=True)
    title = Column(String, nullable=False, default="Untitled Meeting")
    organization_id = Column(String, ForeignKey("organizations.id", ondelete="SET NULL"), nullable=True)
    repository_id = Column(String, ForeignKey("github_repositories.id", ondelete="SET NULL"), nullable=True)
    status = Column(String, nullable=False, default="joining", index=True)  # joining, active, completed, failed, PROCESSING
    error_message = Column(Text, nullable=True)
    
    # Transcription / Provider fields
    platform = Column(String, nullable=False, default="google_meet")
    meeting_url = Column(String, nullable=True)
    native_meeting_id = Column(String, index=True, nullable=True)
    vexa_meeting_id = Column(String, index=True, nullable=True)

    # Transcript Ingestion / Storage / Analysis fields
    source_type = Column(String, nullable=True, default="pasted")  # "file" | "pasted"
    storage_key = Column(String, nullable=True)
    transcript_text = Column(Text, nullable=True)
    analyzed_at = Column(DateTime, nullable=True)
    analysis_status = Column(String, nullable=False, default="pending", index=True)  # pending, processing, completed, failed
    
    started_at = Column(DateTime, nullable=True)
    ended_at = Column(DateTime, nullable=True)
    
    participants_json = Column(JSON, default=list)
    transcript_raw = Column(Text, nullable=True)
    
    # AI Extracted Intelligence
    summary = Column(Text, nullable=True)
    decisions_json = Column(JSON, default=list)
    action_items_json = Column(JSON, default=list)
    facts_count = Column(Integer, default=0)
    
    metadata_json = Column(JSON, default=dict)
    
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)

    # Relationships
    facts = relationship("Fact", foreign_keys="Fact.source_id", primaryjoin="and_(Meeting.id==Fact.source_id, Fact.source_type=='meeting')", backref="meeting", lazy="dynamic")
    decisions = relationship("Decision", back_populates="meeting", cascade="all, delete-orphan")
    tasks = relationship("Task", back_populates="meeting", cascade="all, delete-orphan")
    knowledge_items = relationship("Knowledge", back_populates="meeting", cascade="all, delete-orphan")
