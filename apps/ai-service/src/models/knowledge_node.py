from datetime import datetime
from uuid import uuid4

from pgvector.sqlalchemy import Vector
from sqlalchemy import DateTime, String
from sqlalchemy.orm import Mapped, mapped_column

from src.db.database import Base


class KnowledgeNode(Base):

    __tablename__ = "knowledge_nodes"

    id: Mapped[str] = mapped_column(
        String,
        primary_key=True,
        default=lambda: str(uuid4())
    )

    repository_id: Mapped[str] = mapped_column(
        String,
        nullable=False,
        index=True
    )

    section: Mapped[str] = mapped_column(
        String,
        nullable=False
    )

    topic: Mapped[str] = mapped_column(
        String,
        nullable=False
    )

    fact: Mapped[str] = mapped_column(
        String,
        nullable=False
    )

    embedding: Mapped[list[float]] = mapped_column(
        Vector(1536),
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