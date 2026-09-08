import logging
from typing import Any, Dict, List, Optional
from agents.state import ForgeState
from db.repository import ForgeRepository

logger = logging.getLogger("forge.agent.cron_scheduler")


def get_next_test_node(state: ForgeState) -> Dict[str, Any]:
    """
    GET NEXT TEST node:
    Fetches the next scheduled test scenario to execute from the test_queue.
    If test_queue is uninitialized, populates from test_plan or active tests in database.
    Resets per-test execution telemetry and healing counters.
    """
    if "test_queue" not in state:
        # First time entering Cron Graph: populate queue from test_plan or due tests in database
        plan = state.get("test_plan", [])
        if plan:
            queue = list(plan)
        else:
            target_domain = state.get("target_domain")
            due_tests = ForgeRepository.get_due_tests(domain=target_domain)
            queue = list(due_tests) if due_tests else []
            logger.info(f"[CRON SCHEDULER] Retrieved {len(queue)} due tests from schedule.")
    else:
        queue = list(state.get("test_queue", []))

    if not queue:
        logger.info("[CRON SCHEDULER] Test queue is empty. Cron cycle completed.")
        return {
            "test_queue": [],
            "current_test": None,
            "test_file_path": None,
            "test_code": None,
        }

    # Pop next test from the front of the queue
    next_test = queue.pop(0)
    test_id = str(next_test.get("test_id") or next_test.get("id") or "unknown_test")
    test_file_path = next_test.get("script_path")

    test_code = next_test.get("test_code")
    page_url = next_test.get("page_url") or state.get("target_url")

    # If script_path is missing or not on disk, locate or hydrate from test_code
    from pathlib import Path
    from storage.local import get_website_storage_dir
    site_storage = get_website_storage_dir(page_url or "https://example.com")
    tests_dir = site_storage / "tests"
    tests_dir.mkdir(parents=True, exist_ok=True)

    file_on_disk_exists = test_file_path and Path(test_file_path).exists()

    if not file_on_disk_exists:
        possible_py = tests_dir / f"{test_id}.py"
        if possible_py.exists():
            test_file_path = str(possible_py)
            if not test_code:
                test_code = possible_py.read_text(encoding="utf-8")
        elif test_code:
            # Hydrate test_code to disk so runner can execute it
            possible_py.write_text(test_code, encoding="utf-8")
            test_file_path = str(possible_py)
            logger.info(f"[CRON SCHEDULER] Hydrated test script to disk: {test_file_path}")

    # One stable id for this whole execution cycle (including every heal attempt), so archived
    # script revisions and the resulting test_runs row can be tied together.
    from datetime import datetime, timezone
    run_id = f"run_{test_id}_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}"

    logger.info(
        f"[CRON SCHEDULER] Next test selected: '{test_id}' "
        f"({len(queue)} remaining in queue). Run: {run_id}. Script: {test_file_path}"
    )


    return {
        "test_queue": queue,
        "current_test": next_test,
        "run_id": run_id,
        "test_file_path": test_file_path,
        "test_code": test_code,
        "target_url": page_url,
        "heal_attempt": 0,
        "max_heal_attempts": state.get("max_heal_attempts", 3),
        "healing_history": [],
        "healing_plan": None,
        "execution_result": None,
        "analysis": None,
        "verification_context": None,
        "smoke_result": None,
        "verifier_verdict": None,
        "verifier_reason": None,
    }
