from datetime import datetime
from uuid import uuid4
from typing import Optional, TYPE_CHECKING

from sqlalchemy import DateTime, String, Text, ForeignKey
from sqlalchemy.orm import Mapped, mapped_column, relationship

from src.db.database import Base

if TYPE_CHECKING:
    from models.pull_request import PullRequest
    from models.github_user import GithubUser
    from models.repository import GithubRepository


class Commit(Base):
    __tablename__ = "commits"

    id: Mapped[str] = mapped_column(
        String,
        primary_key=True,
        default=lambda: str(uuid4())
    )

    pull_request_id: Mapped[Optional[str]] = mapped_column(
        String,
        ForeignKey("pull_requests.id", ondelete="CASCADE"),
        nullable=True,
        index=True
    )

    repository_id: Mapped[Optional[str]] = mapped_column(
        String,
        ForeignKey("github_repositories.id", ondelete="CASCADE"),
        nullable=True,
        index=True
    )

    github_commit_sha: Mapped[str] = mapped_column(
        String,
        index=True,
        nullable=False
    )

    author_github_user_id: Mapped[Optional[str]] = mapped_column(
        String,
        ForeignKey("github_users.id", ondelete="SET NULL"),
        nullable=True,
        index=True
    )

    message: Mapped[Optional[str]] = mapped_column(
        Text,
        nullable=True
    )

    committed_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime,
        nullable=True
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime,
        default=datetime.utcnow
    )

    pull_request: Mapped[Optional["PullRequest"]] = relationship("PullRequest", back_populates="commits")
    author: Mapped[Optional["GithubUser"]] = relationship("GithubUser")
    repository: Mapped[Optional["GithubRepository"]] = relationship("GithubRepository")
