from datetime import datetime
from uuid import uuid4
from typing import Optional, Any

from sqlalchemy import DateTime, String, ForeignKey, JSON
from sqlalchemy.orm import Mapped, mapped_column

from src.db.database import Base


class WorkItem(Base):
    __tablename__ = "work_items"

    id: Mapped[str] = mapped_column(
        String,
        primary_key=True,
        default=lambda: str(uuid4())
    )

    title: Mapped[str] = mapped_column(
        String,
        nullable=False,
        index=True  # e.g., "Discussions Redis Caching"
    )

    description: Mapped[Optional[str]] = mapped_column(
        String,
        nullable=True
    )

    assignee_person_id: Mapped[Optional[str]] = mapped_column(
        String,
        ForeignKey("persons.id", ondelete="SET NULL"),
        nullable=True,
        index=True
    )

    status: Mapped[str] = mapped_column(
        String,
        nullable=False,
        default="PLANNED",
        index=True  # 'PLANNED', 'IN_PROGRESS', 'AWAITING_REVIEW', 'CHANGES_REQUESTED', 'COMPLETED', 'ABANDONED'
    )

    area: Mapped[Optional[str]] = mapped_column(
        String,
        nullable=True,
        index=True  # e.g., 'Discussions API', 'Caching'
    )

    metadata_json: Mapped[Optional[dict[str, Any]]] = mapped_column(
        JSON,
        nullable=True
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime,
        default=datetime.utcnow,
        index=True
    )

    updated_at: Mapped[datetime] = mapped_column(
        DateTime,
        default=datetime.utcnow,
        onupdate=datetime.utcnow
    )
