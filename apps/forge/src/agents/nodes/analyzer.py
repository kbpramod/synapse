import json
import logging
from typing import Any, Dict
from agents.llm import get_chat_model
from agents.state import ForgeState, AnalysisResult
from langchain_core.messages import SystemMessage, HumanMessage

logger = logging.getLogger("forge.agent.analyzer")

ANALYZER_SYSTEM_PROMPT = """You are a Principal Test Architect and Defect Analyst.
A Playwright automated test has failed. Your job is to classify whether this is:
1. "NEED_HEAL": A Test Automation Defect (e.g., stale/fragile locator, waiting for element that exists under a different selector, timing issue, frame mismatch, missing wait_for_load_state).
2. "APP_BUG": A True Application Defect (e.g., HTTP 500 Internal Server Error, unhandled JavaScript exception from the app code, broken business logic where correct input resulted in error, missing functionality).

Analyze the error message, stdout, stderr, and the test code.

Return strictly a JSON object:
{
  "verdict": "NEED_HEAL" | "APP_BUG",
  "reason": "Detailed explanation of why the failure occurred",
  "failure_type": "selector_mismatch" | "timeout" | "assertion_failure" | "server_error" | "uncaught_app_exception",
  "suggested_fix": "Concrete suggestion on how to repair the test locator or wait logic, or how to report the bug"
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
        logger.info(f"[ANALYZER] Test '{current_test.get('id')}' PASSED!")
        # Record into suite summary
        suite_summary.append({
            "id": current_test.get("id"),
            "title": current_test.get("title"),
            "status": "PASSED",
            "heals_needed": heal_attempt,
            "duration_s": exec_res.get("duration_s", 0.0),
        })
        return {"analysis": analysis, "suite_summary": suite_summary}

    # Test failed: check heal budget
    if heal_attempt >= max_heal_attempts:
        logger.warning(f"[ANALYZER] Heal budget exceeded ({heal_attempt}/{max_heal_attempts}) for '{current_test.get('id')}'. Flagging failure.")
        analysis = {
            "verdict": "APP_BUG",
            "reason": f"Maximum heal attempts ({max_heal_attempts}) exceeded. Unresolved issue: {exec_res.get('error_summary')}",
            "failure_type": "max_heals_exceeded",
            "suggested_fix": "Manual inspection required; test locators could not auto-align with target page.",
        }
        suite_summary.append({
            "id": current_test.get("id"),
            "title": current_test.get("title"),
            "status": "FAILED_MAX_HEALS",
            "heals_needed": heal_attempt,
            "error": exec_res.get("error_summary"),
        })
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
            HumanMessage(content=f"Failure Telemetry:\n{json.dumps(analyzer_payload, indent=2)}")
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
        verdict = analysis_dict.get("verdict", "NEED_HEAL")
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

    logger.info(f"[ANALYZER] Verdict for '{current_test.get('id')}': {analysis['verdict']} ({analysis.get('reason')})")

    if analysis["verdict"] == "APP_BUG":
        suite_summary.append({
            "id": current_test.get("id"),
            "title": current_test.get("title"),
            "status": "CONFIRMED_BUG",
            "heals_needed": heal_attempt,
            "error": analysis["reason"],
        })

    return {"analysis": analysis, "suite_summary": suite_summary}
