from datetime import datetime
from uuid import uuid4
from typing import Optional, Any

from sqlalchemy import DateTime, String, ForeignKey, JSON
from sqlalchemy.orm import Mapped, mapped_column, relationship

from src.db.database import Base


class GithubEvent(Base):
    __tablename__ = "github_events"

    id: Mapped[str] = mapped_column(
        String,
        primary_key=True,
        default=lambda: str(uuid4())
    )

    installation_id: Mapped[Optional[str]] = mapped_column(
        String,
        ForeignKey("github_installations.id", ondelete="SET NULL"),
        nullable=True,
        index=True
    )

    repository_id: Mapped[Optional[str]] = mapped_column(
        String,
        ForeignKey("github_repositories.id", ondelete="SET NULL"),
        nullable=True,
        index=True
    )

    event_type: Mapped[str] = mapped_column(
        String,
        nullable=False,
        index=True  # e.g., 'pull_request.opened', 'installation.created'
    )

    github_event_id: Mapped[Optional[str]] = mapped_column(
        String,
        nullable=True,
        index=True  # X-GitHub-Delivery / event id
    )

    github_created_at: Mapped[datetime] = mapped_column(
        DateTime,
        default=datetime.utcnow
    )

    received_at: Mapped[datetime] = mapped_column(
        DateTime,
        default=datetime.utcnow
    )

    payload: Mapped[dict[str, Any]] = mapped_column(
        JSON,
        nullable=False
    )
