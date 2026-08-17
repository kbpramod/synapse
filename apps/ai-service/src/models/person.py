from datetime import datetime
from uuid import uuid4
from typing import Optional, Any, List

from sqlalchemy import DateTime, String, Float, ForeignKey, JSON
from sqlalchemy.orm import Mapped, mapped_column, relationship

from src.db.database import Base


class Person(Base):
    __tablename__ = "persons"

    id: Mapped[str] = mapped_column(
        String,
        primary_key=True,
        default=lambda: str(uuid4())
    )

    canonical_name: Mapped[str] = mapped_column(
        String,
        nullable=False,
        index=True  # e.g., 'Pramod'
    )

    primary_email: Mapped[Optional[str]] = mapped_column(
        String,
        nullable=True,
        index=True  # e.g., 'pramod@company.com'
    )

    organization: Mapped[Optional[str]] = mapped_column(
        String,
        nullable=True,
        index=True
    )

    identities: Mapped[List["IdentityLink"]] = relationship(
        "IdentityLink",
        back_populates="person",
        cascade="all, delete-orphan"
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


class IdentityLink(Base):
    __tablename__ = "identity_links"

    id: Mapped[str] = mapped_column(
        String,
        primary_key=True,
        default=lambda: str(uuid4())
    )

    person_id: Mapped[str] = mapped_column(
        String,
        ForeignKey("persons.id", ondelete="CASCADE"),
        nullable=False,
        index=True
    )

    provider: Mapped[str] = mapped_column(
        String,
        nullable=False,
        index=True  # e.g., 'meeting', 'github', 'jira', 'email'
    )

    identity_value: Mapped[str] = mapped_column(
        String,
        nullable=False,
        index=True  # e.g., 'smartcoder', 'Pramod', 'pramod@company.com'
    )

    state: Mapped[str] = mapped_column(
        String,
        nullable=False,
        default="PROBABLE",
        index=True  # 'CONFIRMED', 'PROBABLE', 'UNKNOWN'
    )

    confidence_score: Mapped[float] = mapped_column(
        Float,
        default=0.8
    )

    evidence_json: Mapped[Optional[dict[str, Any]]] = mapped_column(
        JSON,
        nullable=True
    )

    person: Mapped[Person] = relationship(
        "Person",
        back_populates="identities"
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime,
        default=datetime.utcnow
    )
