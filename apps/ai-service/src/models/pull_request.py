from datetime import datetime
from uuid import uuid4
from typing import Optional, List, TYPE_CHECKING

from sqlalchemy import DateTime, Integer, String, Text, ForeignKey
from sqlalchemy.orm import Mapped, mapped_column, relationship

from src.db.database import Base

if TYPE_CHECKING:
    from src.models.repository import GithubRepository
    from src.models.github_user import GithubUser
    from src.models.commit import Commit


class PullRequest(Base):
    __tablename__ = "pull_requests"

    id: Mapped[str] = mapped_column(
        String,
        primary_key=True,
        default=lambda: str(uuid4())
    )

    repository_id: Mapped[str] = mapped_column(
        String,
        ForeignKey("github_repositories.id", ondelete="CASCADE"),
        nullable=False,
        index=True
    )

    github_pr_id: Mapped[str] = mapped_column(
        String,
        unique=True,
        index=True,
        nullable=False
    )

    number: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        index=True
    )

    title: Mapped[str] = mapped_column(
        String,
        nullable=False
    )

    description: Mapped[Optional[str]] = mapped_column(
        Text,
        nullable=True
    )

    author_github_user_id: Mapped[Optional[str]] = mapped_column(
        String,
        ForeignKey("github_users.id", ondelete="SET NULL"),
        nullable=True,
        index=True
    )

    source_branch: Mapped[str] = mapped_column(
        String,
        nullable=False  # e.g., 'feature/add-redis-cache'
    )

    target_branch: Mapped[str] = mapped_column(
        String,
        nullable=False  # e.g., 'develop' or 'main'
    )

    head_sha: Mapped[Optional[str]] = mapped_column(
        String,
        nullable=True
    )

    base_sha: Mapped[Optional[str]] = mapped_column(
        String,
        nullable=True
    )

    status: Mapped[str] = mapped_column(
        String,
        default="open",
        index=True  # 'open', 'closed', 'merged'
    )

    opened_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime,
        nullable=True
    )

    updated_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime,
        nullable=True
    )

    closed_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime,
        nullable=True
    )

    merged_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime,
        nullable=True
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime,
        default=datetime.utcnow
    )

    updated_at_local: Mapped[datetime] = mapped_column(
        DateTime,
        default=datetime.utcnow,
        onupdate=datetime.utcnow
    )

    repository: Mapped["GithubRepository"] = relationship("GithubRepository", back_populates="pull_requests")
    author: Mapped[Optional["GithubUser"]] = relationship("GithubUser")
    commits: Mapped[List["Commit"]] = relationship("Commit", back_populates="pull_request", cascade="all, delete-orphan")
