from datetime import datetime
from uuid import uuid4

from sqlalchemy import DateTime, String, Float, ForeignKey
from sqlalchemy.orm import Mapped, mapped_column

from src.db.database import Base


class EventRelationship(Base):
    __tablename__ = "event_relationships"

    id: Mapped[str] = mapped_column(
        String,
        primary_key=True,
        default=lambda: str(uuid4())
    )

    source_event_id: Mapped[str] = mapped_column(
        String,
        ForeignKey("event_nodes.id", ondelete="CASCADE"),
        nullable=False,
        index=True
    )

    target_event_id: Mapped[str] = mapped_column(
        String,
        ForeignKey("event_nodes.id", ondelete="CASCADE"),
        nullable=False,
        index=True
    )

    relationship_type: Mapped[str] = mapped_column(
        String,
        nullable=False,
        index=True  # e.g., 'implements', 'fulfills', 'discussed_in', 'authored_by', 'followed_by', 'correlates_to'
    )

    confidence_score: Mapped[float] = mapped_column(
        Float,
        default=1.0
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime,
        default=datetime.utcnow
    )
