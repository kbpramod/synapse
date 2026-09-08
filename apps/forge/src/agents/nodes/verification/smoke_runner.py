import logging
from typing import Any, Dict
from agents.state import ExecutionResult, VerificationState
from config import is_headless
from runner.python_runner import run_test_script

logger = logging.getLogger("forge.verification.smoke_runner")


def run_smoke_test_node(state: VerificationState) -> Dict[str, Any]:
    """
    RUN SMOKE TEST node:
    Executes the minimal smoke verification test script in an isolated Playwright subprocess
    to reproduce and capture fresh browser execution evidence.
    """
    smoke_test = state.get("smoke_test") or {}
    script_path = smoke_test.get("script_path")

    if not script_path:
        logger.error("[VERIFICATION - SMOKE RUNNER] No smoke verification script found to execute!")
        return {
            "smoke_result": {
                "exit_code": 1,
                "passed": False,
                "stdout": "",
                "stderr": "No verification smoke script path provided.",
                "duration_s": 0.0,
                "error_summary": "Missing smoke verification script path",
            }
        }

    config = state.get("config", {})
    timeout_s = config.get("verification_timeout_s", 35)
    headless_bool = config.get("headless")
    if headless_bool is None:
        headless_bool = is_headless()

    headless_str = "true" if headless_bool else "false"
    logger.info(
        f"[VERIFICATION - SMOKE RUNNER] Executing verification smoke test: {script_path} "
        f"(timeout={timeout_s}s, headless={headless_str})"
    )

    env_vars = {"HEADLESS": headless_str}
    exec_result = run_test_script(
        script_path,
        timeout_s=timeout_s,
        env_vars=env_vars,
        headed=not headless_bool,
    )

    passed = exec_result.get("passed", False)
    exit_code = exec_result.get("exit_code", -1)
    duration = exec_result.get("duration_s", 0.0)

    logger.info(
        f"[VERIFICATION - SMOKE RUNNER] Execution Completed: Passed={passed}, "
        f"ExitCode={exit_code}, Duration={duration}s"
    )

    return {"smoke_result": exec_result}
