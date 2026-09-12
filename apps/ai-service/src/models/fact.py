from datetime import datetime
from uuid import uuid4
from typing import Optional, Any, TYPE_CHECKING

from pgvector.sqlalchemy import Vector
from sqlalchemy import DateTime, String, Text, ForeignKey, JSON
from sqlalchemy.orm import Mapped, mapped_column, relationship

from src.db.database import Base

if TYPE_CHECKING:
    from models.person import Person
    from models.repository import GithubRepository


class Fact(Base):
    __tablename__ = "facts"

    id: Mapped[str] = mapped_column(
        String,
        primary_key=True,
        default=lambda: str(uuid4())
    )

    content: Mapped[str] = mapped_column(
        Text,
        nullable=False
    )

    embedding: Mapped[Optional[list[float]]] = mapped_column(
        Vector(1536),
        nullable=True
    )

    source_type: Mapped[str] = mapped_column(
        String,
        nullable=False,
        index=True  # e.g., 'github_pr', 'meeting', 'slack'
    )

    source_id: Mapped[str] = mapped_column(
        String,
        nullable=False,
        index=True  # e.g., pull_request.id
    )

    person_id: Mapped[Optional[str]] = mapped_column(
        String,
        ForeignKey("persons.id", ondelete="SET NULL"),
        nullable=True,
        index=True
    )

    repository_id: Mapped[Optional[str]] = mapped_column(
        String,
        ForeignKey("github_repositories.id", ondelete="CASCADE"),
        nullable=True,
        index=True
    )

    fact_status: Mapped[str] = mapped_column(
        String,
        nullable=False,
        default="ACTIVE",
        index=True  # 'ACTIVE', 'SUPERSEDED', 'INVALIDATED'
    )

    work_status: Mapped[str] = mapped_column(
        String,
        nullable=False,
        default="IN_PROGRESS",
        index=True  # 'PROPOSED', 'IN_PROGRESS', 'COMPLETED', 'BLOCKED', 'CANCELLED'
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

    person: Mapped[Optional["Person"]] = relationship("Person")
    repository: Mapped[Optional["GithubRepository"]] = relationship("GithubRepository")
