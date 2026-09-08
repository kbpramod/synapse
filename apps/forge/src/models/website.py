from datetime import datetime, timezone
from typing import TYPE_CHECKING, List, Optional
from sqlalchemy import String, Text, Boolean, DateTime, Integer
from sqlalchemy.orm import Mapped, mapped_column, relationship
from models.base import Base

if TYPE_CHECKING:
    from models.account import Account


class Website(Base):
    __tablename__ = "websites"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    url: Mapped[str] = mapped_column(Text, unique=True, nullable=False, index=True)
    domain: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    app_name: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    environment: Mapped[Optional[str]] = mapped_column(String(50), nullable=True, default="Production")
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    concurrency_limit: Mapped[int] = mapped_column(Integer, default=2, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
        nullable=False,
    )
    last_discovered_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)

    # One-to-many relationship with accounts
    accounts: Mapped[List["Account"]] = relationship(
        "Account", back_populates="website", cascade="all, delete-orphan", lazy="selectin"
    )

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "url": self.url,
            "domain": self.domain,
            "app_name": self.app_name,
            "environment": self.environment,
            "is_active": self.is_active,
            "concurrency_limit": self.concurrency_limit,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
            "last_discovered_at": self.last_discovered_at.isoformat() if self.last_discovered_at else None,
        }
