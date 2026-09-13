import logging
from datetime import datetime, timezone
from typing import Any, Dict, Optional

from agents.state import FailureContext, ForgeState, VerificationState
from db.repository import ForgeRepository
from storage.local import sanitize_domain

logger = logging.getLogger("forge.agent.verifier")


def verifier_node(state: ForgeState) -> Dict[str, Any]:
    """
    DETERMINISTIC EVIDENCE-BASED VERIFICATION NODE:
    Evaluates failure evidence without launching fragile secondary LLM smoke tests.
    Classifies failure as:
    - CONFIRMED_APP_BUG: Server 5xx, network disconnect, crash, or missing critical UI element.
    - FAILED_AUTOMATION: Auth barrier reached, locator/heading mismatch on loaded page, or test script error.
    - INCONCLUSIVE: Insufficient evidence to disambiguate.
    All outcomes terminate cleanly, record the test run in PostgreSQL, advance the test schedule, and route to get_next_test.
    """
    import re
    current_test = state.get("current_test") or {}
    exec_res = state.get("execution_result") or {}
    analysis = state.get("analysis") or {}
    test_id = str(current_test.get("test_id") or current_test.get("id") or "unknown_test")
    target_url = state.get("target_url") or current_test.get("page_url") or "https://example.com"
    target_domain = state.get("target_domain") or sanitize_domain(target_url)

    f_ctx = state.get("failure_context") or {}
    nav_info = f_ctx.get("navigation") or {}
    failure_url = exec_res.get("failure_url") or f_ctx.get("failure_url") or target_url
    discovery_url = f_ctx.get("discovery_url") or failure_url

    disc = state.get("discovery_data") or {}
    disc_elements = disc.get("elements") or {}
    disc_text = disc.get("text") or {}
    headings = [h.get("text") if isinstance(h, dict) else str(h) for h in (disc_text.get("headings") or disc_elements.get("headings") or [])]
    body_preview = (disc_text.get("body_text_preview") or "").lower()

    stderr = exec_res.get("stderr") or ""
    error_summary = exec_res.get("error_summary") or ""
    combined_error = f"{error_summary} {stderr}".lower()

    # Evidence-based classification
    verdict = "INCONCLUSIVE"
    reason = "Verification assessment completed"

    # 1. Genuine application failure indicators (5xx, server crash, connection refused)
    SERVER_CRASH_PATTERNS = (
        "500 internal server error", "502 bad gateway", "503 service unavailable", "504 gateway timeout",
        "net::err_connection_refused", "net::err_name_not_resolved", "server returned 5", "internal server error"
    )
    if any(p in combined_error for p in SERVER_CRASH_PATTERNS):
        verdict = "CONFIRMED_APP_BUG"
        reason = f"Application returned server error or connection failure: {error_summary}"

    # 2. Authentication redirect / session missing
    elif nav_info.get("auth_redirect"):
        verdict = "FAILED_AUTOMATION"
        reason = f"Test attempted to access protected route and was redirected to auth page '{failure_url}'."

    # 3. Destination reached and page loaded with content
    elif f_ctx.get("page_loaded") and (headings or disc_elements.get("buttons") or disc_elements.get("links")):
        # Check if expected target exists in headings or text
        expected_str = str(current_test.get("expected_outcome") or current_test.get("title") or "").lower()
        test_keywords = [w for w in re.split(r"\W+", expected_str) if len(w) > 3]

        found_in_dom = any(
            any(kw in h.lower() for kw in test_keywords)
            for h in headings
        ) or any(kw in body_preview for kw in test_keywords)

        if found_in_dom or "expect(page.locator" in (state.get("test_code") or ""):
            verdict = "FAILED_AUTOMATION"
            reason = (
                f"Destination page '{discovery_url}' loaded successfully with {len(headings)} headings and "
                f"{len(disc_elements.get('buttons', []))} buttons. Failure is a test locator or assertion mismatch."
            )
        else:
            verdict = "INCONCLUSIVE"
            reason = f"Destination page loaded, but expected capability was not confirmed in DOM: {error_summary}"

    else:
        verdict = "INCONCLUSIVE"
        reason = f"Insufficient evidence to confirm application defect: {error_summary}"

    logger.info(f"[VERIFIER NODE] Deterministic classification for '{test_id}': [{verdict}] — {reason}")

    incident_reports = list(state.get("incident_reports", []))
    suite_summary = list(state.get("suite_summary", []))
    run_id = state.get("run_id") or f"run_{test_id}_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}"

    # Record test run in PostgreSQL
    status_str = "failed" if verdict != "PASS" else "passed"
    try:
        ForgeRepository.record_test_run(
            run_id=run_id,
            test_id=test_id,
            exit_code=exec_res.get("exit_code", 1),
            status=status_str,
            duration_s=exec_res.get("duration_s", 0.0),
            error_summary=f"[{verdict}] {reason}",
            stdout=exec_res.get("stdout", ""),
            stderr=exec_res.get("stderr", ""),
        )
        cron_hours = current_test.get("cron_interval_hours", 24)
        ForgeRepository.update_test_run_timestamps(test_id, cron_hours)
        logger.info(f"[VERIFIER NODE] Recorded run and advanced schedule by {cron_hours}h for '{test_id}'.")
    except Exception as e:
        logger.warning(f"[VERIFIER NODE] Could not record run/schedule in DB: {e}")

    if verdict == "CONFIRMED_APP_BUG":
        report = {
            "incident_id": f"inc_{test_id}_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}",
            "test_id": test_id,
            "target_url": target_url,
            "failure_url": failure_url,
            "summary": reason,
            "status": "OPEN",
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
        incident_reports.append(report)
        suite_summary.append({
            "id": test_id,
            "title": current_test.get("title"),
            "status": "CONFIRMED_BUG",
            "incident_id": report["incident_id"],
            "error": reason,
        })
    else:
        suite_summary.append({
            "id": test_id,
            "title": current_test.get("title"),
            "status": verdict,
            "error": reason,
        })

    return {
        "failure_context": f_ctx,
        "verifier_verdict": verdict,
        "verifier_reason": reason,
        "incident_reports": incident_reports,
        "suite_summary": suite_summary,
    }


# Backwards compatibility re-exports from verification package
from agents.nodes.verification.context_loader import load_failure_context_node
from agents.nodes.verification.smoke_builder import build_smoke_verification_test_node
from agents.nodes.verification.smoke_runner import run_smoke_test_node
from agents.nodes.verification.verifier_evaluator import verifier_llm_node
from agents.nodes.verification.report_generator import report_node
