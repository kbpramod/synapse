from datetime import datetime
from uuid import uuid4
from sqlalchemy import Column, String, Text, DateTime, JSON, Integer, ForeignKey
from sqlalchemy.orm import relationship

from src.db.database import Base


class Meeting(Base):
    __tablename__ = "meetings"

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
