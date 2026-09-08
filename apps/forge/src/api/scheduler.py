"""
FastAPI router for Forge Scalable Distributed Scheduler.

Exposes:
- Queue metrics and running tests inspection.
- Upcoming scheduled runs.
- Cohort de-bunching / window spacing distribution.
- Immediate priority test triggers.
- Daemon and worker pool lifecycle management.
"""
import asyncio
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional
from fastapi import APIRouter, BackgroundTasks, HTTPException, Query, status
from pydantic import BaseModel

from db.repository import ForgeRepository
from scheduler.daemon import (
    is_scheduler_daemon_running,
    run_scheduler_tick,
    start_scheduler_daemon,
    stop_scheduler_daemon,
)
from scheduler.queue import queue_manager
from scheduler.worker import (
    is_worker_pool_running,
    start_in_process_worker_pool,
    stop_in_process_worker_pool,
)

router = APIRouter(prefix="/scheduler", tags=["Distributed Test Scheduler"])


class DistributeRequest(BaseModel):
    website_id: Optional[int] = None


class DaemonControlRequest(BaseModel):
    poll_interval_seconds: int = 5
    batch_size: int = 50


class WorkerControlRequest(BaseModel):
    num_workers: int = 2


class UpdateTestScheduleRequest(BaseModel):
    enabled: Optional[bool] = None
    cron_expression: Optional[str] = None
    timezone: Optional[str] = None
    priority: Optional[str] = None
    schedule_offset_seconds: Optional[int] = None


# ==========================================
# Status & Metrics
# ==========================================
@router.get("/status")
def get_scheduler_status():
    """
    Returns real-time distributed scheduler health, queue depths,
    active workers count, and currently running tests.
    """
    metrics = queue_manager.get_metrics()
    running_tests = queue_manager.list_running_jobs()

    return {
        "daemon_running": is_scheduler_daemon_running(),
        "worker_pool_running": is_worker_pool_running(),
        "queue_metrics": metrics,
        "running_tests": running_tests,
    }


@router.get("/upcoming")
def get_upcoming_runs(
    limit: int = Query(default=50, ge=1, le=500),
    domain: Optional[str] = None,
):
    """
    Returns upcoming tests scheduled for execution, sorted chronologically
    by their next target execution timestamp (next_run_at).
    """
    upcoming = ForgeRepository.get_upcoming_tests(limit=limit, domain=domain)
    now_utc = datetime.now(timezone.utc)

    enriched = []
    for t in upcoming:
        next_run = t.get("next_run_at")
        time_until_s = None
        if next_run:
            if isinstance(next_run, str):
                try:
                    dt = datetime.fromisoformat(next_run)
                    time_until_s = (dt - now_utc).total_seconds()
                except Exception:
                    pass
            elif hasattr(next_run, "tzinfo"):
                time_until_s = (next_run - now_utc).total_seconds()

        enriched.append({
            **t,
            "seconds_until_due": round(time_until_s, 1) if time_until_s is not None else None,
            "is_due_now": (time_until_s is not None and time_until_s <= 0),
        })

    return {
        "count": len(enriched),
        "domain_filter": domain,
        "upcoming_tests": enriched,
    }


@router.get("/metrics")
def get_scheduler_metrics(hours: int = Query(default=24, ge=1, le=168)):
    """
    Combines Redis ephemeral queue metrics with PostgreSQL test run telemetry.
    """
    queue_metrics = queue_manager.get_metrics()
    db_summary = ForgeRepository.get_regression_summary(hours=hours)

    return {
        "window_hours": hours,
        "queue": queue_metrics,
        "execution_summary": db_summary,
    }


# ==========================================
# De-Bunching & Scheduling Management
# ==========================================
@router.post("/distribute")
def distribute_test_schedules(request: DistributeRequest = DistributeRequest()):
    """
    Distributes test execution targets (next_run_at) across their recurrence window
    for tests sharing the same frequency cadence.
    Prevents execution spikes at exact cron boundaries (e.g. 100 hourly tests
    spaced roughly 36s apart across 3600s).
    """
    result = ForgeRepository.distribute_tests_schedule(website_id=request.website_id)
    return {
        "status": "success",
        "message": f"Distributed {result.get('updated_count')} tests across {result.get('cohorts_count')} cohorts.",
        "details": result,
    }


@router.post("/trigger/{test_id}")
def trigger_test_immediate(test_id: str):
    """
    Immediately claims and enqueues a specific test into Redis with 'critical' priority,
    bypassing its regular schedule window without waiting.
    """
    test_row = ForgeRepository.get_test_by_id(test_id)
    if not test_row:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Test '{test_id}' not found.",
        )

    run_id = f"manual_{uuid.uuid4().hex[:10]}"
    job = {
        "run_id": run_id,
        "test_id": test_row["test_id"],
        "website_id": test_row.get("website_id"),
        "domain": test_row.get("domain"),
        "page_url": test_row.get("page_url"),
        "title": test_row.get("title"),
        "priority": "critical",
        "script_path": test_row.get("script_path"),
        "language": test_row.get("language") or "python",
        "concurrency_limit": 2,
        "enqueued_at": datetime.now(timezone.utc).isoformat(),
        "manual_trigger": True,
    }

    success = queue_manager.enqueue(job, priority="critical")
    if not success:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to enqueue test into Redis.",
        )

    return {
        "status": "enqueued",
        "run_id": run_id,
        "test_id": test_id,
        "priority": "critical",
    }


@router.patch("/tests/{test_id}")
def update_test_schedule(test_id: str, request: UpdateTestScheduleRequest):
    """Updates scheduling parameters (enabled, cron, timezone, priority) for a test."""
    updated = ForgeRepository.update_test_scheduler_config(
        test_id=test_id,
        enabled=request.enabled,
        cron_expression=request.cron_expression,
        timezone=request.timezone,
        priority=request.priority,
        schedule_offset_seconds=request.schedule_offset_seconds,
    )
    if not updated:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Test '{test_id}' not found or no changes made.",
        )
    return {"status": "updated", "test_id": test_id}


# ==========================================
# Lifecycle Management (Daemon & Workers)
# ==========================================
@router.post("/tick")
def trigger_claim_tick(batch_size: int = Query(default=50, ge=1, le=200)):
    """Executes a single atomic claim-and-enqueue tick on demand."""
    return run_scheduler_tick(batch_size=batch_size)


@router.post("/daemon/start")
def start_daemon_endpoint(
    request: DaemonControlRequest, background_tasks: BackgroundTasks
):
    """Starts the continuous background claimer daemon."""
    if is_scheduler_daemon_running():
        return {
            "status": "already_running",
            "message": "Scheduler daemon is already running.",
        }

    background_tasks.add_task(
        start_scheduler_daemon,
        poll_interval_seconds=request.poll_interval_seconds,
        batch_size=request.batch_size,
    )
    return {
        "status": "started",
        "poll_interval_seconds": request.poll_interval_seconds,
        "batch_size": request.batch_size,
    }


@router.post("/daemon/stop")
def stop_daemon_endpoint():
    """Stops the continuous background claimer daemon."""
    stopped = stop_scheduler_daemon()
    return {
        "status": "stopped" if stopped else "not_running",
        "message": "Scheduler daemon stopped." if stopped else "Daemon was not running.",
    }


@router.post("/worker/start")
async def start_worker_pool_endpoint(request: WorkerControlRequest):
    """Starts an in-process worker pool inside the FastAPI service."""
    if is_worker_pool_running():
        return {
            "status": "already_running",
            "message": "Worker pool is already running.",
        }

    await start_in_process_worker_pool(num_workers=request.num_workers)
    return {
        "status": "started",
        "num_workers": request.num_workers,
    }


@router.post("/worker/stop")
def stop_worker_pool_endpoint():
    """Stops the in-process worker pool."""
    stopped = stop_in_process_worker_pool()
    return {
        "status": "stopped" if stopped else "not_running",
        "message": "Worker pool stopped." if stopped else "Worker pool was not running.",
    }
