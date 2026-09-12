from datetime import datetime
from uuid import uuid4
from typing import TYPE_CHECKING, List, Optional

from sqlalchemy import DateTime, ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from src.db.database import Base

if TYPE_CHECKING:
    from models.organization import Organization
    from models.user import User
    from models.repository import GithubRepository


class GithubInstallation(Base):
    __tablename__ = "github_installations"

    id: Mapped[str] = mapped_column(
        String,
        primary_key=True,
        default=lambda: str(uuid4())
    )

    organization_id: Mapped[Optional[str]] = mapped_column(
        String,
        ForeignKey("organizations.id", ondelete="CASCADE"),
        nullable=True,
        index=True
    )

    user_id: Mapped[Optional[str]] = mapped_column(
        String,
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
        index=True
    )

    github_installation_id: Mapped[str] = mapped_column(
        String,
        unique=True,
        nullable=False,
        index=True
    )

    github_account_id: Mapped[Optional[str]] = mapped_column(
        String,
        nullable=True
    )

    github_account_login: Mapped[Optional[str]] = mapped_column(
        String,
        nullable=True
    )

    github_account_type: Mapped[Optional[str]] = mapped_column(
        String,
        nullable=True
    )

    installed_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime,
        default=datetime.utcnow,
        nullable=True
    )

    uninstalled_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime,
        nullable=True
    )

    status: Mapped[str] = mapped_column(
        String,
        default="active",
        nullable=False
    )  # e.g., "active", "suspended", "deleted"

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
    organization: Mapped[Optional["Organization"]] = relationship(
        "Organization",
        back_populates="installations"
    )

    repositories: Mapped[List["GithubRepository"]] = relationship(
        "GithubRepository",
        back_populates="github_installation",
        cascade="all, delete-orphan"
    )

    # Backward compatibility properties
    @property
    def installation_id(self) -> str:
        return self.github_installation_id

    @installation_id.setter
    def installation_id(self, value: str):
        self.github_installation_id = value

    @property
    def account_id(self) -> Optional[str]:
        return self.github_account_id

    @account_id.setter
    def account_id(self, value: Optional[str]):
        self.github_account_id = value

    @property
    def account_login(self) -> Optional[str]:
        return self.github_account_login

    @account_login.setter
    def account_login(self, value: Optional[str]):
        self.github_account_login = value

    @property
    def account_type(self) -> Optional[str]:
        return self.github_account_type

    @account_type.setter
    def account_type(self, value: Optional[str]):
        self.github_account_type = value
