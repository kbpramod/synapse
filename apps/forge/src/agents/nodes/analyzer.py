import json
import logging
import re
from pathlib import Path
from typing import Any, Dict, Optional
from urllib.parse import urlparse
from agents.llm import get_chat_model
from agents.state import ForgeState, AnalysisResult
from langchain_core.messages import SystemMessage, HumanMessage

logger = logging.getLogger("forge.agent.analyzer")

_FINAL_URL_RE = re.compile(r"\[FINAL_URL\]\s*(\S+)")


def _extract_final_url(stdout: str) -> Optional[str]:
    """Pulls the `[FINAL_URL] <url>` marker every generated test script prints
    right before declaring success (see builder.py's system prompts/templates)."""
    match = _FINAL_URL_RE.search(stdout or "")
    return match.group(1).strip() if match else None


def _normalize_url(url: str) -> str:
    parsed = urlparse(url)
    return f"{parsed.scheme}://{parsed.netloc}{parsed.path.rstrip('/')}"


def _storage_state_path_for(test_file_path: Optional[str]) -> Optional[str]:
    """Path of the session file the test script saves next to itself right before closing
    (see builder.py's templates). Returns None when it wasn't produced."""
    if not test_file_path:
        return None
    candidate = Path(test_file_path).with_suffix("").as_posix() + ".storage_state.json"
    return candidate if Path(candidate).exists() else None


def _maybe_onboard_new_page(
    final_url: str,
    started_from: Optional[str],
    test_file_path: Optional[str] = None,
    website_id: Optional[int] = None,
) -> None:
    """
    If a passing test ended up on a page different from where it started (e.g. a login
    flow landing on a dashboard), fire the onboarding graph for that page too, so it gets
    its own discovered elements, understanding, and generated tests — unless it's already
    been onboarded.

    The test's browser is already closed by this point, so the authenticated session it
    saved just before closing is handed to discovery; without it, a page behind a login
    would just redirect the fresh browser back to the login screen.
    """
    if not final_url or not started_from or _normalize_url(final_url) == _normalize_url(started_from):
        return

    from db.repository import ForgeRepository
    if ForgeRepository.has_test_for_page(final_url):
        logger.info(f"[ANALYZER] '{final_url}' already has tests onboarded; skipping.")
        return

    # Carry the parent site's website_id so the new page inherits exactly that site's
    # accounts, rather than re-resolving by URL and risking another site on the same domain.
    if website_id is None:
        website_id = ForgeRepository.resolve_website_id(started_from)

    storage_state_path = _storage_state_path_for(test_file_path)
    logger.info(
        f"[ANALYZER] Test navigated from '{started_from}' to a new page '{final_url}'; "
        f"triggering onboarding for it (website_id={website_id}, "
        f"{'reusing saved session' if storage_state_path else 'no saved session available'})."
    )
    from agents.onboarding_graph import run_onboarding_graph_background
    run_onboarding_graph_background(
        final_url,
        website_id=website_id,
        storage_state_path=storage_state_path,
    )

ANALYZER_SYSTEM_PROMPT = """You are a Principal Test Architect and User Journey Defect Analyst.
A Playwright automated user journey test has executed. Your job is to answer:
"Did the user journey succeed?" and classify the result strictly based on evidence:

1. "PASS": The user capability succeeded and all functional state transitions were confirmed.
2. "NEED_HEAL": A Test Automation Defect. The application may be functioning, but the automated test script failed.
   Evidence examples:
   - Locator resolved to a hidden responsive variant (e.g., 'locator resolved to hidden <span>Contact Us</span>' - the element exists, but the test selected the hidden responsive variant or failed to open the menu drawer first).
   - Locator not found or wrong selector.
   - Test code error (missing import, NameError, wrong Playwright API usage).
   - Timing/waiting issue on dynamic DOM elements.
3. "SUSPECTED_APP_FAILURE": A Potential Genuine Application Defect. The automation attempted interaction, but the application failed.
   Evidence examples:
   - HTTP 500 / 502 / 503 Internal Server Error from backend.
   - Uncaught application JavaScript exception originating from the application code.
   - Application crash or broken business logic (e.g., valid form submission triggered an unexpected error page).

Analyze the test scenario goal, error summary, stderr, stdout, and test code.
Return strictly a JSON object:
{
  "verdict": "PASS" | "NEED_HEAL" | "SUSPECTED_APP_FAILURE",
  "reason": "Detailed explanation of whether the user journey succeeded or why it failed based on evidence",
  "failure_type": "hidden_responsive_variant" | "selector_mismatch" | "test_code_error" | "timeout" | "assertion_failure" | "server_error" | "uncaught_app_exception",
  "suggested_fix": "Concrete guidance for the healer on how to repair the automation, or suspected bug details"
}
Output ONLY valid JSON.
"""


def analyzer_node(state: ForgeState) -> Dict[str, Any]:
    """
    ANALYZER node: Evaluates execution outcomes.
    Decides if the test passed, needs self-healing (selector/timing fix),
    or uncovered a real application bug.
    """
    exec_res = state.get("execution_result") or {}
    current_test = state.get("current_test") or {}
    heal_attempt = state.get("heal_attempt", 0)
    max_heal_attempts = state.get("max_heal_attempts", 3)
    suite_summary = list(state.get("suite_summary", []))

    if exec_res.get("passed", False):
        analysis: AnalysisResult = {
            "verdict": "PASS",
            "reason": "Test executed and assertions passed successfully.",
            "failure_type": None,
            "suggested_fix": None,
        }
        test_id = str(current_test.get("test_id") or current_test.get("id") or "unknown_test")
        logger.info(f"[ANALYZER] Test '{test_id}' PASSED!")

        # Record into suite summary
        suite_summary.append({
            "id": test_id,
            "title": current_test.get("title"),
            "status": "PASSED",
            "heals_needed": heal_attempt,
            "duration_s": exec_res.get("duration_s", 0.0),
        })

        # Record test run and update cron timestamps in PostgreSQL
        try:
            from datetime import datetime, timezone
            from db.repository import ForgeRepository
            # Reuse the cycle's run_id so this row ties to the archived script revisions.
            run_id = state.get("run_id") or f"run_{test_id}_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}"
            ForgeRepository.record_test_run(
                run_id=run_id,
                test_id=test_id,
                exit_code=0,
                status="passed",
                duration_s=exec_res.get("duration_s", 0.0),
                stdout=exec_res.get("stdout", ""),
                stderr=exec_res.get("stderr", ""),
                screenshot_paths=exec_res.get("screenshot_paths", []),
                trace_path=exec_res.get("trace_path"),
            )
            cron_hours = current_test.get("cron_interval_hours", 24)
            ForgeRepository.update_test_run_timestamps(test_id, cron_hours)
            logger.info(f"[ANALYZER] Indexed PASS in test_runs and advanced next_run_at by {cron_hours}h.")
        except Exception as db_err:
            logger.warning(f"[ANALYZER] Could not update PostgreSQL run metrics: {db_err}")

        # If the passing journey navigated to a page never onboarded before (e.g. a login
        # flow reaching a dashboard), extend coverage to it automatically.
        try:
            final_url = _extract_final_url(exec_res.get("stdout", ""))
            started_from = current_test.get("page_url") or state.get("target_url")
            _maybe_onboard_new_page(
                final_url,
                started_from,
                state.get("test_file_path"),
                website_id=state.get("website_id") or current_test.get("website_id"),
            )
        except Exception as e:
            logger.warning(f"[ANALYZER] Could not evaluate new-page onboarding: {e}")

        return {"analysis": analysis, "suite_summary": suite_summary}


    # Test failed: check heal budget
    if heal_attempt >= max_heal_attempts:
        logger.warning(f"[ANALYZER] Heal budget exceeded ({heal_attempt}/{max_heal_attempts}) for '{current_test.get('id')}'. Routing to verification.")
        analysis = {
            "verdict": "SUSPECTED_APP_FAILURE",
            "reason": f"Maximum heal attempts ({max_heal_attempts}) reached without resolution: {exec_res.get('error_summary')}",
            "failure_type": "max_heals_exceeded",
            "suggested_fix": "Verify if application UI or backend changed unexpectedly.",
        }
        return {"analysis": analysis, "suite_summary": suite_summary}

    # Analyze failure cause with LLM
    stderr = exec_res.get("stderr", "")
    stdout = exec_res.get("stdout", "")
    test_code = state.get("test_code", "")

    analyzer_payload = {
        "test_id": current_test.get("id"),
        "test_title": current_test.get("title"),
        "error_summary": exec_res.get("error_summary"),
        "stderr": stderr[-2000:],
        "stdout": stdout[-1000:],
        "test_code": test_code[-2000:],
        "heal_attempt": heal_attempt,
    }

    try:
        llm = get_chat_model()
        messages = [
            SystemMessage(content=ANALYZER_SYSTEM_PROMPT),
            HumanMessage(content=f"Failure Telemetry:\n{json.dumps(analyzer_payload, indent=2, default=str)}")
        ]
        response = llm.invoke(messages)
        content = response.content.strip()

        if content.startswith("```"):
            lines = content.splitlines()
            if lines[0].startswith("```"):
                lines = lines[1:]
            if lines and lines[-1].startswith("```"):
                lines = lines[:-1]
            content = "\n".join(lines).strip()

        analysis_dict = json.loads(content)
        raw_verdict = str(analysis_dict.get("verdict", "NEED_HEAL")).strip().upper()
        if raw_verdict in ("APP_BUG", "SUSPECTED_APP_FAILURE"):
            verdict = "SUSPECTED_APP_FAILURE"
        elif raw_verdict == "PASS":
            verdict = "PASS"
        else:
            verdict = "NEED_HEAL"

        analysis = {
            "verdict": verdict,
            "reason": analysis_dict.get("reason", "Detected defect requiring resolution."),
            "failure_type": analysis_dict.get("failure_type", "unknown"),
            "suggested_fix": analysis_dict.get("suggested_fix"),
        }
    except Exception as e:
        logger.warning(f"[ANALYZER] LLM defect analysis failed ({e}). Defaulting to NEED_HEAL heuristic.")
        analysis = {
            "verdict": "NEED_HEAL",
            "reason": f"Execution error: {exec_res.get('error_summary') or 'Timeout/locator failure'}",
            "failure_type": "selector_or_timing",
            "suggested_fix": "Refine locators and adjust wait times.",
        }

    # Prefer the test_id slug over the numeric primary key, which is meaningless in logs.
    log_test_id = current_test.get("test_id") or current_test.get("id")
    logger.info(f"[ANALYZER] Verdict for '{log_test_id}': {analysis['verdict']} ({analysis.get('reason')})")

    if analysis["verdict"] == "SUSPECTED_APP_FAILURE":
        suite_summary.append({
            "id": current_test.get("id"),
            "title": current_test.get("title"),
            "status": "SUSPECTED_APP_FAILURE",
            "heals_needed": heal_attempt,
            "error": analysis["reason"],
        })

    return {"analysis": analysis, "suite_summary": suite_summary}
