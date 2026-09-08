from datetime import datetime, timezone
from typing import Any, Dict, List, Optional
from sqlalchemy import (
    String,
    Text,
    Integer,
    Float,
    DateTime,
    ForeignKey,
    UniqueConstraint,
    Index,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column
from models.base import Base


class Page(Base):
    __tablename__ = "pages"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    domain: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    url: Mapped[str] = mapped_column(Text, unique=True, nullable=False)
    title: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    slug: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    page_type: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    purpose: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    primary_actions: Mapped[Optional[List[Dict[str, Any]]]] = mapped_column(JSONB, nullable=True)
    state_preconditions: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    discovered_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), nullable=False
    )

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "domain": self.domain,
            "url": self.url,
            "title": self.title,
            "slug": self.slug,
            "page_type": self.page_type,
            "purpose": self.purpose,
            "primary_actions": self.primary_actions,
            "state_preconditions": self.state_preconditions,
            "discovered_at": self.discovered_at.isoformat() if self.discovered_at else None,
        }


class Element(Base):
    __tablename__ = "elements"
    __table_args__ = (
        UniqueConstraint("forge_id", "page_url", name="uq_forge_element"),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    forge_id: Mapped[str] = mapped_column(String(100), nullable=False, index=True)
    page_url: Mapped[str] = mapped_column(Text, nullable=False)
    tag: Mapped[str] = mapped_column(String(50), nullable=False)
    element_type: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)
    text: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    selector: Mapped[str] = mapped_column(Text, nullable=False)
    bounding_box: Mapped[Optional[Dict[str, Any]]] = mapped_column(JSONB, nullable=True)
    discovered_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), nullable=False
    )

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "forge_id": self.forge_id,
            "page_url": self.page_url,
            "tag": self.tag,
            "element_type": self.element_type,
            "text": self.text,
            "selector": self.selector,
            "bounding_box": self.bounding_box,
            "discovered_at": self.discovered_at.isoformat() if self.discovered_at else None,
        }


class Test(Base):
    __tablename__ = "tests"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    test_id: Mapped[str] = mapped_column(String(100), unique=True, nullable=False)
    website_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("websites.id", ondelete="CASCADE"), nullable=True, index=True
    )
    domain: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    page_url: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    title: Mapped[str] = mapped_column(Text, nullable=False)
    description: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    category: Mapped[str] = mapped_column(String(50), default="regression", nullable=False)
    priority: Mapped[str] = mapped_column(String(20), default="medium", nullable=False)
    steps: Mapped[Optional[List[str]]] = mapped_column(JSONB, nullable=True)
    expected_outcome: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    script_path: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    test_code: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    language: Mapped[str] = mapped_column(String(20), default="python", nullable=False)
    status: Mapped[str] = mapped_column(String(50), default="active", nullable=False, index=True)
    
    # Cron & Scheduling configuration
    cron_interval_hours: Mapped[Optional[int]] = mapped_column(Integer, default=24, nullable=True, index=True)
    cron_expression: Mapped[Optional[str]] = mapped_column(String(100), default="0 0 * * *", nullable=True)
    last_run_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    next_run_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True, index=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
        nullable=False,
    )

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "test_id": self.test_id,
            "website_id": self.website_id,
            "domain": self.domain,
            "page_url": self.page_url,
            "title": self.title,
            "description": self.description,
            "category": self.category,
            "priority": self.priority,
            "steps": self.steps,
            "expected_outcome": self.expected_outcome,
            "script_path": self.script_path,
            "test_code": self.test_code,
            "language": self.language,
            "status": self.status,
            "cron_interval_hours": self.cron_interval_hours,
            "cron_expression": self.cron_expression,
            "last_run_at": self.last_run_at.isoformat() if self.last_run_at else None,
            "next_run_at": self.next_run_at.isoformat() if self.next_run_at else None,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }


class TestRun(Base):
    __tablename__ = "test_runs"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    run_id: Mapped[str] = mapped_column(String(100), nullable=False)
    test_id: Mapped[str] = mapped_column(String(100), nullable=False, index=True)
    exit_code: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(String(50), nullable=False)
    duration_s: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    error_summary: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    stdout: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    stderr: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    screenshot_paths: Mapped[Optional[List[str]]] = mapped_column(JSONB, nullable=True)
    trace_path: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    executed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        nullable=False,
        index=True,
    )

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "run_id": self.run_id,
            "test_id": self.test_id,
            "exit_code": self.exit_code,
            "status": self.status,
            "duration_s": self.duration_s,
            "error_summary": self.error_summary,
            "screenshot_paths": self.screenshot_paths,
            "trace_path": self.trace_path,
            "executed_at": self.executed_at.isoformat() if self.executed_at else None,
        }


class Heal(Base):
    __tablename__ = "heals"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    test_id: Mapped[str] = mapped_column(String(100), nullable=False)
    run_id: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    attempt: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    error_snippet: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    diagnosis: Mapped[str] = mapped_column(Text, nullable=False)
    fix_plan: Mapped[str] = mapped_column(Text, nullable=False)
    healed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), nullable=False
    )

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "test_id": self.test_id,
            "run_id": self.run_id,
            "attempt": self.attempt,
            "error_snippet": self.error_snippet,
            "diagnosis": self.diagnosis,
            "fix_plan": self.fix_plan,
            "healed_at": self.healed_at.isoformat() if self.healed_at else None,
        }
