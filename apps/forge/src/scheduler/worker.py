"""
Independent Playwright test execution worker for Forge.

Consumes jobs from Redis priority queues, enforces per-website concurrency limits,
executes tests via the test execution pipeline, and persists results to PostgreSQL.
"""
import asyncio
import logging
import os
import signal
import sys
import time
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, Optional

from agents.cron_runner import run_single_test
from config import is_headless
from scheduler.queue import queue_manager

logger = logging.getLogger("forge.scheduler.worker")

_worker_running = False
_worker_tasks: list[asyncio.Task] = []


def execute_job(job: Dict[str, Any], worker_id: str) -> Dict[str, Any]:
    """
    Executes a dequeued test job:
    1. Checks and acquires per-website concurrency limit.
    2. Registers job as running in Redis.
    3. Runs the test with Playwright.
    4. Automatically releases concurrency slot and unregisters on completion.
    """
    run_id = job.get("run_id") or f"run_{uuid.uuid4().hex[:12]}"
    test_id = job.get("test_id")
    website_id = job.get("website_id")
    concurrency_limit = job.get("concurrency_limit") or 2

    # 1. Check per-website concurrency limit
    acquired = queue_manager.try_acquire_concurrency(
        website_id=website_id,
        run_id=run_id,
        limit=concurrency_limit,
    )
    if not acquired:
        logger.info(
            f"[WORKER {worker_id}] Website {website_id} concurrency limit ({concurrency_limit}) "
            f"reached. Deferring test '{test_id}' for 3.0s."
        )
        queue_manager.delay_job(job, delay_seconds=3.0, reason=f"website_{website_id}_concurrency_cap")
        return {"status": "deferred", "reason": "concurrency_limit"}

    # 2. Register job as running
    queue_manager.register_running_job(
        run_id=run_id,
        job_info={
            "test_id": test_id,
            "website_id": website_id,
            "domain": job.get("domain"),
            "title": job.get("title"),
            "worker_id": worker_id,
            "priority": job.get("priority"),
        },
    )

    logger.info(
        f"[WORKER {worker_id}] Started executing test '{test_id}' (run_id: {run_id}, "
        f"domain: {job.get('domain')})..."
    )

    start_time = time.time()
    try:
        # 3. Execute test through Forge execution pipeline
        headless_mode = is_headless()
        result = run_single_test(test_id=test_id, headless=headless_mode)
        duration = time.time() - start_time
        logger.info(
            f"[WORKER {worker_id}] Finished test '{test_id}' in {duration:.2f}s: "
            f"status={result.get('status')}"
        )
        return {
            "status": "completed",
            "run_id": run_id,
            "test_id": test_id,
            "duration_s": round(duration, 2),
            "result": result,
        }
    except Exception as e:
        duration = time.time() - start_time
        logger.error(
            f"[WORKER {worker_id}] Unhandled error executing test '{test_id}': {e}",
            exc_info=True,
        )
        return {
            "status": "failed",
            "run_id": run_id,
            "test_id": test_id,
            "error": str(e),
            "duration_s": round(duration, 2),
        }
    finally:
        # 4. Strictly release concurrency and unregister running job
        queue_manager.release_concurrency(website_id=website_id, run_id=run_id)
        queue_manager.unregister_running_job(run_id=run_id)


def worker_loop_sync(worker_id: str, poll_timeout_s: int = 2) -> None:
    """Synchronous worker loop that continuously drains jobs from Redis."""
    global _worker_running
    logger.info(f"[WORKER {worker_id}] Worker loop started.")

    while _worker_running:
        try:
            queue_manager.beat_worker(worker_id)
            job = queue_manager.dequeue(timeout=poll_timeout_s)
            if not job:
                continue

            execute_job(job, worker_id=worker_id)
        except Exception as e:
            logger.error(f"[WORKER {worker_id}] Error in worker loop: {e}", exc_info=True)
            time.sleep(1)

    logger.info(f"[WORKER {worker_id}] Worker loop terminated cleanly.")


async def start_in_process_worker_pool(num_workers: int = 2) -> None:
    """Starts an in-process pool of worker threads inside the FastAPI process."""
    global _worker_running, _worker_tasks
    _worker_running = True
    _worker_tasks.clear()

    logger.info(f"[WORKER POOL] Starting {num_workers} in-process workers...")
    loop = asyncio.get_running_loop()

    for i in range(num_workers):
        worker_id = f"worker_{uuid.uuid4().hex[:6]}_{i+1}"
        task = loop.run_in_executor(None, worker_loop_sync, worker_id, 2)
        _worker_tasks.append(task)


def stop_in_process_worker_pool() -> bool:
    """Signals all running in-process workers to stop."""
    global _worker_running
    if _worker_running:
        _worker_running = False
        logger.info("[WORKER POOL] Stop signal dispatched to all workers.")
        return True
    return False


def is_worker_pool_running() -> bool:
    """Checks if the in-process worker pool is currently active."""
    return _worker_running


# CLI Entrypoint for Standalone Workers
if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Forge Test Execution Worker")
    parser.add_argument("--workers", type=int, default=2, help="Number of concurrent worker threads")
    parser.add_argument("--id-prefix", type=str, default="cli", help="Worker ID prefix")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        stream=sys.stdout,
    )

    _worker_running = True

    def handle_signal(sig, frame):
        logger.info(f"Received signal {sig}, stopping workers...")
        stop_in_process_worker_pool()

    signal.signal(signal.SIGINT, handle_signal)
    signal.signal(signal.SIGTERM, handle_signal)

    from concurrent.futures import ThreadPoolExecutor

    worker_prefix = f"{args.id_prefix}_{os.getpid()}"
    logger.info(f"Starting standalone worker pool: {args.workers} worker(s)...")

    with ThreadPoolExecutor(max_workers=args.workers) as executor:
        for i in range(args.workers):
            wid = f"{worker_prefix}_{i+1}"
            executor.submit(worker_loop_sync, wid, 2)
