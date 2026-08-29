from datetime import datetime
from uuid import uuid4
from typing import TYPE_CHECKING

from sqlalchemy import DateTime, ForeignKey, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from src.db.database import Base

if TYPE_CHECKING:
    from src.models.organization import Organization
    from models.user import User


class OrganizationMember(Base):
    __tablename__ = "organization_members"

    id: Mapped[str] = mapped_column(
        String,
        primary_key=True,
        default=lambda: str(uuid4())
    )

    organization_id: Mapped[str] = mapped_column(
        String,
        ForeignKey("organizations.id", ondelete="CASCADE"),
        nullable=False,
        index=True
    )

    user_id: Mapped[str] = mapped_column(
        String,
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True
    )

    role: Mapped[str] = mapped_column(
        String,
        default="member",
        nullable=False
    )  # e.g., "owner", "admin", "member"

    created_at: Mapped[datetime] = mapped_column(
        DateTime,
        default=datetime.utcnow
    )

    updated_at: Mapped[datetime] = mapped_column(
        DateTime,
        default=datetime.utcnow,
        onupdate=datetime.utcnow
    )

    # Constraints & Relationships
    __table_args__ = (
        UniqueConstraint("organization_id", "user_id", name="uq_org_member"),
    )

    organization: Mapped["Organization"] = relationship(
        "Organization",
        back_populates="members"
    )

    user: Mapped["User"] = relationship(
        "User",
        back_populates="organization_memberships"
    )
