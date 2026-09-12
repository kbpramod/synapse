from datetime import datetime
from uuid import uuid4
from typing import TYPE_CHECKING, List

from sqlalchemy import Boolean, DateTime, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from src.db.database import Base

if TYPE_CHECKING:
    from models.organization_member import OrganizationMember


class User(Base):
    __tablename__ = "users"
    __table_args__ = {'extend_existing': True}

    id: Mapped[str] = mapped_column(
        String,
        primary_key=True,
        default=lambda: str(uuid4())
    )

    clerk_user_id: Mapped[str] = mapped_column(
        String,
        unique=True,
        nullable=False,
        index=True
    )

    email: Mapped[str | None] = mapped_column(
        String,
        nullable=True
    )

    name: Mapped[str | None] = mapped_column(
        String,
        nullable=True
    )

    is_verified: Mapped[bool] = mapped_column(
        Boolean,
        default=True,
        nullable=False
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
    organization_memberships: Mapped[List["OrganizationMember"]] = relationship(
        "OrganizationMember",
        back_populates="user",
        cascade="all, delete-orphan"
    )

    @property
    def organization_id(self) -> str | None:
        if hasattr(self, "_active_organization_id") and self._active_organization_id:
            return self._active_organization_id
        if self.organization_memberships:
            return self.organization_memberships[0].organization_id
        return None

    @organization_id.setter
    def organization_id(self, value: str | None) -> None:
        self._active_organization_id = value

