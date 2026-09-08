import asyncio
import logging
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from agents.cron_graph import create_cron_graph
from agents.state import ForgeState
from db.repository import ForgeRepository

logger = logging.getLogger("forge.cron_runner")

_daemon_running = False
_daemon_task: Optional[asyncio.Task] = None


def run_cron_cycle(
    domain: Optional[str] = None,
    headless: bool = False,
    limit: int = 50,
) -> Dict[str, Any]:
    """
    Executes a single cron cycle:
    1. Queries the PostgreSQL `tests` table for tests due for execution based on their schedule.
    2. Feeds due tests into CRON GRAPH — V1.
    3. Returns structured metrics on the completed cycle.
    """
    cycle_start = datetime.now(timezone.utc)
    logger.info(
        f"[CRON RUNNER] Starting cron execution cycle at {cycle_start.isoformat()} "
        f"(Domain filter: {domain or 'All Domains'})..."
    )

    # 1. Retrieve due tests from database
    due_tests = ForgeRepository.get_due_tests(domain=domain, limit=limit)

    if not due_tests:
        logger.info("[CRON RUNNER] No tests are currently due for execution.")
        return {
            "status": "idle",
            "due_count": 0,
            "executed_count": 0,
            "started_at": cycle_start.isoformat(),
            "completed_at": datetime.now(timezone.utc).isoformat(),
            "summary": [],
        }

    test_ids = [t.get("test_id") or t.get("id") for t in due_tests]
    logger.info(f"[CRON RUNNER] Found {len(due_tests)} due test(s): {test_ids}")

    # 2. Prepare initial state for Cron Graph
    target_url = due_tests[0].get("page_url") or "https://example.com"
    initial_state: ForgeState = {
        "target_url": target_url,
        "target_domain": domain,
        "test_queue": due_tests,
        "config": {"headless": headless},
        "suite_summary": [],
        "incident_reports": [],
    }

    # 3. Execute Cron Graph
    graph = create_cron_graph()
    final_state = graph.invoke(initial_state)

    suite_summary = final_state.get("suite_summary", [])
    incidents = final_state.get("incident_reports", [])
    cycle_end = datetime.now(timezone.utc)
    duration_s = (cycle_end - cycle_start).total_seconds()

    passed_count = sum(1 for s in suite_summary if s.get("status") == "PASSED")
    bug_count = len(incidents)
    failed_count = sum(1 for s in suite_summary if s.get("status") == "FAILED_AUTOMATION")

    logger.info(
        f"[CRON RUNNER] Cron cycle completed in {duration_s:.2f}s: "
        f"Total={len(due_tests)}, Passed={passed_count}, BugsConfirmed={bug_count}, "
        f"FailedAutomation={failed_count}"
    )

    return {
        "status": "completed",
        "due_count": len(due_tests),
        "executed_count": len(suite_summary),
        "passed_count": passed_count,
        "bug_count": bug_count,
        "failed_count": failed_count,
        "duration_seconds": round(duration_s, 2),
        "started_at": cycle_start.isoformat(),
        "completed_at": cycle_end.isoformat(),
        "suite_summary": suite_summary,
        "incident_reports": incidents,
    }


def run_single_test(test_id: str, headless: bool = False) -> Dict[str, Any]:
    """
    Executes exactly one test immediately, regardless of its cron schedule.
    Used by the "Run Now" action so a test doesn't have to wait for its due time.
    """
    run_start = datetime.now(timezone.utc)
    test_row = ForgeRepository.get_test_by_id(test_id)

    if not test_row:
        return {
            "status": "not_found",
            "test_id": test_id,
            "message": f"No test found with id '{test_id}'.",
        }

    logger.info(f"[CRON RUNNER] Running test on demand: {test_id}")

    initial_state: ForgeState = {
        "target_url": test_row.get("page_url") or "https://example.com",
        "target_domain": test_row.get("domain"),
        "test_queue": [test_row],
        "config": {"headless": headless},
        "suite_summary": [],
        "incident_reports": [],
    }

    graph = create_cron_graph()
    final_state = graph.invoke(initial_state)

    suite_summary = final_state.get("suite_summary", [])
    incidents = final_state.get("incident_reports", [])
    run_end = datetime.now(timezone.utc)

    result = suite_summary[0] if suite_summary else {
        "id": test_id,
        "title": test_row.get("title"),
        "status": "FAILED_AUTOMATION",
        "error": "Test did not produce a result.",
    }

    logger.info(f"[CRON RUNNER] On-demand run of '{test_id}' completed: {result.get('status')}")

    return {
        "status": "completed",
        "test_id": test_id,
        "result": result,
        "incident_reports": incidents,
        "started_at": run_start.isoformat(),
        "completed_at": run_end.isoformat(),
        "duration_seconds": round((run_end - run_start).total_seconds(), 2),
    }


async def start_cron_scheduler_daemon(
    poll_interval_seconds: int = 60,
    domain: Optional[str] = None,
    headless: bool = False,
) -> None:
    """
    Continuous background daemon that periodically checks the PostgreSQL tests table
    and triggers test execution cycles whenever tests become due.
    """
    global _daemon_running
    _daemon_running = True
    logger.info(
        f"[CRON DAEMON] Starting continuous test scheduler daemon. "
        f"Poll Interval: {poll_interval_seconds}s | Domain: {domain or 'All'}"
    )

    while _daemon_running:
        try:
            # Run cycle in worker thread so sync database/browser execution doesn't block event loop
            loop = asyncio.get_running_loop()
            await loop.run_in_executor(
                None,
                run_cron_cycle,
                domain,
                headless,
                50,
            )
        except Exception as e:
            logger.error(f"[CRON DAEMON] Error during cron scheduler cycle: {e}", exc_info=True)

        # Sleep until next poll interval
        for _ in range(poll_interval_seconds):
            if not _daemon_running:
                break
            await asyncio.sleep(1)

    logger.info("[CRON DAEMON] Continuous test scheduler daemon stopped gracefully.")


def stop_cron_scheduler_daemon() -> bool:
    """Stops the running continuous scheduler daemon."""
    global _daemon_running
    if _daemon_running:
        _daemon_running = False
        logger.info("[CRON DAEMON] Stop signal dispatched.")
        return True
    return False


def is_daemon_running() -> bool:
    """Checks if the cron scheduler daemon is currently running."""
    return _daemon_running
