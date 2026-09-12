from datetime import datetime
from uuid import uuid4
from typing import Optional, TYPE_CHECKING

from sqlalchemy import DateTime, String, ForeignKey
from sqlalchemy.orm import Mapped, mapped_column, relationship

from src.db.database import Base

if TYPE_CHECKING:
    from models.person import Person


class GithubUser(Base):
    __tablename__ = "github_users"

    id: Mapped[str] = mapped_column(
        String,
        primary_key=True,
        default=lambda: str(uuid4())
    )

    github_user_id: Mapped[str] = mapped_column(
        String,
        unique=True,
        index=True,
        nullable=False
    )

    username: Mapped[str] = mapped_column(
        String,
        index=True,
        nullable=False  # e.g., 'smartcoder'
    )

    display_name: Mapped[Optional[str]] = mapped_column(
        String,
        nullable=True
    )

    email: Mapped[Optional[str]] = mapped_column(
        String,
        nullable=True,
        index=True
    )

    avatar_url: Mapped[Optional[str]] = mapped_column(
        String,
        nullable=True
    )

    person_id: Mapped[Optional[str]] = mapped_column(
        String,
        ForeignKey("persons.id", ondelete="SET NULL"),
        nullable=True,
        index=True
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
