from datetime import datetime
from uuid import uuid4
from typing import TYPE_CHECKING, List, Optional

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from src.db.database import Base

if TYPE_CHECKING:
    from src.models.github_installation import GithubInstallation
    from src.models.user import User
    from src.models.doc_page import DocPage


class GithubRepository(Base):
    __tablename__ = "github_repositories"

    id: Mapped[str] = mapped_column(
        String,
        primary_key=True,
        default=lambda: str(uuid4())
    )

    installation_id: Mapped[Optional[str]] = mapped_column(
        String,
        ForeignKey("github_installations.id", ondelete="CASCADE"),
        nullable=True,
        index=True
    )

    github_repository_id: Mapped[Optional[str]] = mapped_column(
        String,
        nullable=True,
        index=True
    )

    name: Mapped[str] = mapped_column(
        String,
        nullable=False
    )

    full_name: Mapped[Optional[str]] = mapped_column(
        String,
        nullable=True
    )

    private: Mapped[bool] = mapped_column(
        Boolean,
        default=False,
        nullable=False
    )

    active: Mapped[bool] = mapped_column(
        Boolean,
        default=True,
        nullable=False
    )

    # Additional metadata fields for AI Service & Indexing
    user_id: Mapped[Optional[str]] = mapped_column(
        String,
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
        index=True
    )

    owner: Mapped[Optional[str]] = mapped_column(
        String,
        nullable=True
    )

    status: Mapped[str] = mapped_column(
        String,
        default="Ready",
        nullable=False
    )  # Enum: "Ready" | "Indexing" | "Syncing" | "Failed"

    last_sync: Mapped[str] = mapped_column(
        String,
        default="Just now",
        nullable=False
    )

    knowledge_nodes_count: Mapped[int] = mapped_column(
        Integer,
        default=0,
        nullable=False
    )

    doc_pages_count: Mapped[int] = mapped_column(
        Integer,
        default=0,
        nullable=False
    )

    github_url: Mapped[Optional[str]] = mapped_column(
        String,
        nullable=True
    )

    connected_at: Mapped[Optional[str]] = mapped_column(
        String,
        default=lambda: datetime.utcnow().strftime("%Y-%m-%d"),
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

    # Relationships
    github_installation: Mapped[Optional["GithubInstallation"]] = relationship(
        "GithubInstallation",
        back_populates="repositories"
    )

    doc_pages: Mapped[List["DocPage"]] = relationship(
        "DocPage",
        back_populates="repository",
        cascade="all, delete-orphan"
    )

    # Backward compatibility property for github_installation_id
    @property
    def github_installation_id(self) -> Optional[str]:
        return self.installation_id

    @github_installation_id.setter
    def github_installation_id(self, value: Optional[str]):
        self.installation_id = value


# Alias for backward compatibility
Repository = GithubRepository
