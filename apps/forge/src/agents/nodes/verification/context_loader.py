import logging
from typing import Any, Dict, List, Optional
from agents.state import FailureContext, VerificationState

logger = logging.getLogger("forge.verification.context_loader")


def load_failure_context_node(state: VerificationState) -> Dict[str, Any]:
    """
    LOAD FAILURE CONTEXT node:
    Normalizes and gathers the original failure evidence (expected vs actual, failed step,
    console errors, network errors, stack trace, screenshot) from the test run that triggered
    the suspected application failure.
    """
    existing_ctx = state.get("failure_context") or {}
    failed_test_id = state.get("failed_test_id", "unknown_test")

    logger.info(f"[VERIFICATION - CONTEXT] Loading failure evidence for '{failed_test_id}'...")

    expected = existing_ctx.get("expected", "Expected application capability/state transition to succeed.")
    actual = existing_ctx.get("actual") or existing_ctx.get("error", "Application did not transition or returned an error.")
    failed_step = existing_ctx.get("failed_step", "Execution of test interaction step")
    error = existing_ctx.get("error")
    screenshot = existing_ctx.get("screenshot")
    trace = existing_ctx.get("trace")

    console_errors: List[str] = list(existing_ctx.get("console_errors", []))
    network_errors: List[str] = list(existing_ctx.get("network_errors", []))

    # Parse stderr/stdout for additional console and network errors if present
    raw_error = error or ""
    if "500" in raw_error or "502" in raw_error or "503" in raw_error or "504" in raw_error:
        if not network_errors:
            network_errors.append(f"HTTP Server Error detected in error summary: {raw_error[:200]}")
    if "UnhandledPromiseRejection" in raw_error or "ReferenceError" in raw_error or "TypeError" in raw_error:
        if not console_errors:
            console_errors.append(f"Uncaught JavaScript Exception: {raw_error[:200]}")

    failure_ctx: FailureContext = {
        "expected": expected,
        "actual": actual,
        "failed_step": failed_step,
        "error": error,
        "screenshot": screenshot,
        "trace": trace,
        "console_errors": console_errors,
        "network_errors": network_errors,
    }

    logger.info(
        f"[VERIFICATION - CONTEXT] Failure Context Loaded: "
        f"FailedStep='{failed_step}' | ConsoleErrors={len(console_errors)} | NetworkErrors={len(network_errors)}"
    )

    return {"failure_context": failure_ctx}
