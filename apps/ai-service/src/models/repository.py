from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from src.db.database import Base


class Repository(Base):
    __tablename__ = "repositories"

    id: Mapped[str] = mapped_column(
        String,
        primary_key=True
    )

    user_id: Mapped[str] = mapped_column(
        String,
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True
    )

    github_installation_id: Mapped[str | None] = mapped_column(
        String,
        ForeignKey("github_installations.id", ondelete="SET NULL"),
        nullable=True,
        index=True
    )

    github_repository_id: Mapped[str | None] = mapped_column(
        String,
        nullable=True
    )

    owner: Mapped[str | None] = mapped_column(
        String,
        nullable=True
    )

    full_name: Mapped[str | None] = mapped_column(
        String,
        nullable=True
    )

    name: Mapped[str] = mapped_column(
        String,
        nullable=False
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

    github_url: Mapped[str] = mapped_column(
        String,
        nullable=False
    )

    connected_at: Mapped[str] = mapped_column(
        String,
        default=lambda: datetime.utcnow().strftime("%Y-%m-%d"),
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
