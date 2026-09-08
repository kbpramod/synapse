import asyncio
from typing import Any, Dict, List, Optional
from fastapi import APIRouter, BackgroundTasks, Query
from pydantic import BaseModel

from fastapi import HTTPException, status

from agents.cron_runner import (
    is_daemon_running,
    run_cron_cycle,
    run_single_test,
    start_cron_scheduler_daemon,
    stop_cron_scheduler_daemon,
)
from db.repository import ForgeRepository

router = APIRouter(prefix="/cron", tags=["Cron Scheduler"])


class CronRunRequest(BaseModel):
    domain: Optional[str] = None
    headless: bool = False
    limit: int = 50


class RunTestRequest(BaseModel):
    headless: bool = False


class DaemonStartRequest(BaseModel):
    interval_seconds: int = 60
    domain: Optional[str] = None
    headless: bool = False


@router.get("/due")
def get_due_tests(domain: Optional[str] = None, limit: int = 50):
    """Returns active tests currently due for execution based on their cron schedule."""
    due = ForgeRepository.get_due_tests(domain=domain, limit=limit)
    return {
        "count": len(due),
        "domain_filter": domain,
        "due_tests": due,
    }


@router.get("/schedule")
def get_test_schedules(domain: Optional[str] = None):
    """Returns all active tests alongside their cron intervals and last/next execution timestamps."""
    all_tests = ForgeRepository.get_active_tests(domain=domain)
    due_tests = ForgeRepository.get_due_tests(domain=domain)
    due_ids = {t["test_id"] for t in due_tests}

    schedule_list = []
    for t in all_tests:
        schedule_list.append({
            "test_id": t["test_id"],
            "title": t["title"],
            "domain": t["domain"],
            "cron_interval_hours": t.get("cron_interval_hours", 24),
            "cron_expression": t.get("cron_expression"),
            "last_run_at": t.get("last_run_at"),
            "next_run_at": t.get("next_run_at"),
            "is_due": t["test_id"] in due_ids,
            "status": t.get("status"),
        })

    return {
        "total_active": len(all_tests),
        "due_count": len(due_tests),
        "schedules": schedule_list,
    }


@router.post("/run")
def trigger_cron_cycle(request: CronRunRequest):
    """Triggers an on-demand execution cycle for tests that are due based on their cron schedule."""
    result = run_cron_cycle(
        domain=request.domain,
        headless=request.headless,
        limit=request.limit,
    )
    return result


@router.post("/run/{test_id}")
def trigger_single_test(test_id: str, request: RunTestRequest = RunTestRequest()):
    """Runs a single test immediately, bypassing its cron schedule (the "Run Now" action)."""
    result = run_single_test(test_id=test_id, headless=request.headless)
    if result["status"] == "not_found":
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=result["message"])
    return result


@router.get("/daemon/status")
def get_daemon_status():
    """Checks whether the continuous background cron scheduler is actively running."""
    return {"daemon_running": is_daemon_running()}


@router.post("/daemon/start")
def start_daemon(request: DaemonStartRequest, background_tasks: BackgroundTasks):
    """Starts the background continuous scheduler daemon."""
    if is_daemon_running():
        return {"status": "already_running", "message": "Cron scheduler daemon is already running."}

    background_tasks.add_task(
        start_cron_scheduler_daemon,
        poll_interval_seconds=request.interval_seconds,
        domain=request.domain,
        headless=request.headless,
    )
    return {
        "status": "started",
        "poll_interval_seconds": request.interval_seconds,
        "domain": request.domain,
    }


@router.post("/daemon/stop")
def stop_daemon():
    """Stops the background continuous scheduler daemon."""
    stopped = stop_cron_scheduler_daemon()
    return {
        "status": "stopped" if stopped else "not_running",
        "message": "Cron scheduler daemon stopped." if stopped else "Daemon was not running.",
    }
