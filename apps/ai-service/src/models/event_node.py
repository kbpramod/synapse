from datetime import datetime
from uuid import uuid4
from typing import Optional, Any

from sqlalchemy import DateTime, String, JSON, ForeignKey
from sqlalchemy.orm import Mapped, mapped_column

from src.db.database import Base


class EventNode(Base):
    __tablename__ = "event_nodes"

    id: Mapped[str] = mapped_column(
        String,
        primary_key=True,
        default=lambda: str(uuid4())
    )

    source: Mapped[str] = mapped_column(
        String,
        nullable=False,
        index=True  # e.g., 'meeting', 'github', 'manual'
    )

    event_type: Mapped[str] = mapped_column(
        String,
        nullable=False,
        index=True  # e.g., 'meeting_discussion', 'meeting_decision', 'meeting_commitment', 'pr_created', 'pr_updated', 'review_requested', 'review_submitted', 'changes_requested', 'pr_approved', 'pr_merged', 'pr_closed'
    )

    timestamp: Mapped[datetime] = mapped_column(
        DateTime,
        nullable=False,
        default=datetime.utcnow,
        index=True
    )

    actor: Mapped[Optional[str]] = mapped_column(
        String,
        nullable=True,
        index=True  # raw actor handle/name (e.g., 'Pramod', 'smartcoder')
    )

    person_id: Mapped[Optional[str]] = mapped_column(
        String,
        ForeignKey("persons.id", ondelete="SET NULL"),
        nullable=True,
        index=True  # resolved canonical person ID
    )

    work_item_id: Mapped[Optional[str]] = mapped_column(
        String,
        ForeignKey("work_items.id", ondelete="SET NULL"),
        nullable=True,
        index=True  # linked work item ID
    )

    entity_type: Mapped[Optional[str]] = mapped_column(
        String,
        nullable=True,
        index=True  # e.g., 'commitment', 'pull_request', 'decision', 'work_item'
    )

    entity_id: Mapped[Optional[str]] = mapped_column(
        String,
        nullable=True,
        index=True  # e.g., 'PR #182', 'meeting-123'
    )

    content: Mapped[str] = mapped_column(
        String,
        nullable=False
    )

    reference_id: Mapped[Optional[str]] = mapped_column(
        String,
        nullable=True,
        index=True  # external delivery ID or meeting segment ID for idempotency
    )

    metadata_json: Mapped[Optional[dict[str, Any]]] = mapped_column(
        JSON,
        nullable=True
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime,
        default=datetime.utcnow
    )

    updated_at: Mapped[datetime] = mapped_column(
        DateTime,
        default=datetime.utcnow,
        onupdate=datetime.utcnow
    )
