"""
Periodic distributed claimer daemon for Forge.

Finds due tests in PostgreSQL, atomically claims them using FOR UPDATE SKIP LOCKED
(guaranteeing mutually exclusive claims across multiple scheduler instances),
advances their `next_run_at`, and enqueues execution jobs into Redis.
"""
import asyncio
import logging
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from config import SCHEDULER_BATCH_SIZE, SCHEDULER_POLL_INTERVAL_S
from db.repository import ForgeRepository
from scheduler.queue import queue_manager

logger = logging.getLogger("forge.scheduler.daemon")

_scheduler_running = False
_scheduler_task: Optional[asyncio.Task] = None
_scheduler_id = f"sched_{uuid.uuid4().hex[:8]}"


def run_scheduler_tick(batch_size: int = SCHEDULER_BATCH_SIZE) -> Dict[str, Any]:
    """
    Executes a single claim-and-enqueue tick:
    1. Atomically claims due tests from PostgreSQL using FOR UPDATE SKIP LOCKED.
    2. Enqueues claimed tests into Redis priority queues.
    3. Requeues ready jobs from the delayed backoff queue.
    4. Sends scheduler heartbeat.
    """
    # 1. Update heartbeat
    queue_manager.beat_scheduler(_scheduler_id)

    # 2. Requeue delayed backoff jobs
    requeued_count = queue_manager.requeue_delayed_jobs(limit=50)

    # 3. Atomically claim due tests from PostgreSQL
    try:
        claimed_tests = ForgeRepository.claim_due_tests(batch_size=batch_size)
    except Exception as e:
        logger.error(f"[SCHEDULER] Failed to claim due tests from database: {e}", exc_info=True)
        return {
            "status": "error",
            "error": str(e),
            "claimed_count": 0,
            "requeued_count": requeued_count,
        }

    if not claimed_tests:
        return {
            "status": "idle",
            "claimed_count": 0,
            "requeued_count": requeued_count,
        }

    # 4. Enqueue into Redis
    enqueued_count = 0
    for test in claimed_tests:
        run_id = f"run_{uuid.uuid4().hex[:12]}"
        job = {
            "run_id": run_id,
            "test_id": test["test_id"],
            "website_id": test.get("website_id"),
            "domain": test.get("domain"),
            "page_url": test.get("page_url"),
            "title": test.get("title"),
            "priority": test.get("priority") or "medium",
            "script_path": test.get("script_path"),
            "language": test.get("language") or "python",
            "concurrency_limit": test.get("concurrency_limit") or 2,
            "enqueued_at": datetime.now(timezone.utc).isoformat(),
        }
        success = queue_manager.enqueue(job, priority=job["priority"])
        if success:
            enqueued_count += 1

    logger.info(
        f"[SCHEDULER TICK] Claimed {len(claimed_tests)} tests, enqueued {enqueued_count} to Redis, "
        f"requeued {requeued_count} delayed."
    )

    return {
        "status": "active",
        "claimed_count": len(claimed_tests),
        "enqueued_count": enqueued_count,
        "requeued_count": requeued_count,
        "test_ids": [t["test_id"] for t in claimed_tests],
    }


async def start_scheduler_daemon(
    poll_interval_seconds: int = SCHEDULER_POLL_INTERVAL_S,
    batch_size: int = SCHEDULER_BATCH_SIZE,
) -> None:
    """Continuous background loop running scheduler claim ticks."""
    global _scheduler_running
    _scheduler_running = True
    logger.info(
        f"[SCHEDULER DAEMON] Started scheduler daemon (ID: {_scheduler_id}, "
        f"Poll: {poll_interval_seconds}s, Batch: {batch_size})."
    )

    while _scheduler_running:
        try:
            loop = asyncio.get_running_loop()
            await loop.run_in_executor(None, run_scheduler_tick, batch_size)
        except Exception as e:
            logger.error(f"[SCHEDULER DAEMON] Unhandled error during tick: {e}", exc_info=True)

        for _ in range(poll_interval_seconds):
            if not _scheduler_running:
                break
            await asyncio.sleep(1)

    logger.info(f"[SCHEDULER DAEMON] Scheduler daemon ({_scheduler_id}) stopped gracefully.")


def stop_scheduler_daemon() -> bool:
    """Signals the scheduler daemon to stop."""
    global _scheduler_running
    if _scheduler_running:
        _scheduler_running = False
        logger.info("[SCHEDULER DAEMON] Stop signal dispatched.")
        return True
    return False


def is_scheduler_daemon_running() -> bool:
    """Returns True if the background scheduler daemon is active."""
    return _scheduler_running


def get_scheduler_id() -> str:
    """Returns the identifier of this scheduler instance."""
    return _scheduler_id
