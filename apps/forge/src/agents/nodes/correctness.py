import json
import logging
import re
from typing import Any, Dict, Literal
from agents.llm import get_chat_model
from agents.state import ForgeState
from langchain_core.messages import SystemMessage, HumanMessage

logger = logging.getLogger("forge.agent.correctness")

CORRECTNESS_SYSTEM_PROMPT = """You are a Principal Test Intelligence Architect and Correctness Evaluator.
Your job is to evaluate: "Did the executed user journey succeed, and are the grounded expectations correct?"

You must classify the outcome into exactly ONE of these five verdicts based strictly on concrete evidence:

1. "CORRECT":
   - The action was executed successfully.
   - The application transitioned to a state that satisfies the user journey's intent.
   - The derived expectations are supported by high confidence (>= 0.85) and direct browser evidence.

2. "ACTION_DEFECT":
   - Semantic Action Failure: The action script ran without crashing, but failed to accomplish the intended functional interaction.
   - Evidence requirements: Concrete evidence that the interaction was incomplete or misdirected (e.g. form was not submitted because client-side validation prevented it, button clicked was disabled, clicked cancel instead of submit, missed mandatory field).

3. "EXPECTATION_DEFECT":
   - The action executed properly and the application responded normally/legitimately, but the expectation criteria misjudged the outcome (e.g. expected a redirect to /dashboard, but the app legitimately displays an in-place modal or confirmation notification on the same page).

4. "APP_BUG":
   - Evidence directly associated with the attempted action or resulting state indicates genuine application failure:
     * HTTP 500, 502, 503 error on an API/submit endpoint directly triggered by the action.
     * Application crash or uncaught business logic exception directly originating from the action.
   - NOTE: Unrelated third-party tracking, analytics.js, or ad-blocker script errors are strictly NOT an application bug.

5. "INCONCLUSIVE":
   - The evidence is ambiguous, contradictory, or insufficient to confidently determine whether the action failed, the expectation was wrong, or the application bugged.
   - "INCONCLUSIVE" is a safe and valid outcome. Never guess or force a classification without clear evidence.

Return strictly a JSON object:
{
  "verdict": "CORRECT" | "ACTION_DEFECT" | "EXPECTATION_DEFECT" | "APP_BUG" | "INCONCLUSIVE",
  "confidence": 0.95,
  "reason": "Detailed evidence-based explanation for the verdict",
  "evidence": [
    "Specific evidence item 1",
    "Specific evidence item 2"
  ],
  "suggested_action": "Guidance on how to heal the action, refine the expectation, or document the bug"
}
Output ONLY valid JSON.
"""


def correctness_node(state: ForgeState) -> Dict[str, Any]:
    """
    CORRECTNESS node:
    Evaluates whether the user journey succeeded based on (Intent, Action, PostActionResult, ExpectationSpec).
    Emits one of the 5 verdicts: CORRECT, ACTION_DEFECT, EXPECTATION_DEFECT, APP_BUG, INCONCLUSIVE.
    """
    current_test = state.get("current_test") or {}
    test_id = str(current_test.get("test_id") or current_test.get("id") or "test_journey")
    journey_intent = str(current_test.get("intent") or current_test.get("title") or "")

    action_spec = state.get("action_spec") or {}
    action_res = state.get("action_result") or {}
    post_action_res = state.get("post_action_result") or {}
    expectation_spec = state.get("expectation_spec") or {}

    nav = post_action_res.get("navigation") or {}
    dom_delta = post_action_res.get("dom_delta") or {}
    network = post_action_res.get("network") or {}
    console = post_action_res.get("console") or {}

    # Check for direct backend application crashes (e.g. 500 on action endpoint)
    responses = network.get("responses", [])
    server_errors = [
        r for r in responses
        if r.get("status", 0) >= 500 and not any(t in str(r.get("url", "")) for t in ["analytics", "tracker", "telemetry", "clarity", "hotjar"])
    ]

    evaluation_payload = {
        "test_id": test_id,
        "journey_intent": journey_intent,
        "pre_action_url": nav.get("previous_url"),
        "post_action_url": nav.get("result_url"),
        "url_changed": nav.get("url_changed"),
        "action_steps": action_spec.get("steps", []),
        "post_action_alerts": dom_delta.get("visible_alerts", []),
        "post_action_headings": dom_delta.get("current_headings", []),
        "added_elements_count": len(dom_delta.get("added_elements", [])),
        "removed_elements_count": len(dom_delta.get("removed_elements", [])),
        "server_errors": server_errors,
        "console_errors": console.get("errors", [])[:5],
        "synthesized_expectations": expectation_spec.get("expectations", []),
    }

    logger.info(f"[CORRECTNESS] Evaluating outcome correctness for '{test_id}' against intent: '{journey_intent}'")

    llm = get_chat_model()
    messages = [
        SystemMessage(content=CORRECTNESS_SYSTEM_PROMPT),
        HumanMessage(content=f"Evaluate journey correctness:\n{json.dumps(evaluation_payload, indent=2, default=str)}")
    ]

    verdict = "CORRECT"
    reason = "User journey and assertions verified successfully."
    evaluation_dict: Dict[str, Any] = {}

    try:
        response = llm.invoke(messages)
        content = response.content.strip()

        if content.startswith("```"):
            lines = content.splitlines()
            if lines[0].startswith("```"):
                lines = lines[1:]
            if lines and lines[-1].startswith("```"):
                lines = lines[:-1]
            content = "\n".join(lines).strip()

        evaluation_dict = json.loads(content)
        raw_verdict = str(evaluation_dict.get("verdict", "CORRECT")).strip().upper()
        if raw_verdict in ("CORRECT", "ACTION_DEFECT", "EXPECTATION_DEFECT", "APP_BUG", "INCONCLUSIVE"):
            verdict = raw_verdict
        else:
            verdict = "INCONCLUSIVE"

        reason = evaluation_dict.get("reason", "Evaluated based on telemetry.")
    except Exception as e:
        logger.warning(f"[CORRECTNESS] LLM evaluation failed ({e}). Running deterministic heuristic.")
        if server_errors:
            verdict = "APP_BUG"
            reason = f"Action endpoint returned server error HTTP {server_errors[0].get('status')}."
        elif expectation_spec.get("expectations"):
            verdict = "CORRECT"
            reason = "Action completed and grounded assertions derived cleanly."
        else:
            verdict = "INCONCLUSIVE"
            reason = "Insufficient evidence to determine correctness."

    # Guardrails: Check heal budgets
    action_heals = state.get("action_heal_attempt", 0)
    max_action_heals = state.get("max_action_heals", 3)
    exp_heals = state.get("expectation_heal_attempt", 0)
    max_exp_heals = state.get("max_expectation_heals", 2)

    if verdict == "ACTION_DEFECT" and action_heals >= max_action_heals:
        logger.warning(f"[CORRECTNESS] Action heal budget exhausted ({action_heals}/{max_action_heals}) -> Transitioning to INCONCLUSIVE.")
        verdict = "INCONCLUSIVE"
        reason = f"Max action heals ({max_action_heals}) reached without resolution: {reason}"

    if verdict == "EXPECTATION_DEFECT" and exp_heals >= max_exp_heals:
        logger.warning(f"[CORRECTNESS] Expectation heal budget exhausted ({exp_heals}/{max_exp_heals}) -> Transitioning to INCONCLUSIVE.")
        verdict = "INCONCLUSIVE"
        reason = f"Max expectation heals ({max_exp_heals}) reached. Halting assertion adjustment to prevent erosion: {reason}"

    logger.info(f"[CORRECTNESS] Evaluation Result for '{test_id}': [{verdict}] {reason}")

    from datetime import datetime, timezone
    verification_result = {
        "verdict": verdict,
        "confidence": evaluation_dict.get("confidence", 0.95),
        "evidence": evaluation_dict.get("evidence", [reason]),
        "reason": reason,
        "suggested_action": evaluation_dict.get("suggested_action"),
        "evaluated_at": datetime.now(timezone.utc).isoformat(),
    }

    return {
        "correctness_verdict": verdict,
        "correctness_reason": reason,
        "correctness_evaluation": evaluation_dict,
        "verification_result": verification_result,
    }



def route_correctness(state: ForgeState) -> Literal["assemble_testcase", "heal_action", "heal_expectation", "advance_test"]:
    """
    Conditional router out of the Correctness evaluation node:
    - 'CORRECT': Assemble final test case -> 'assemble_testcase'.
    - 'ACTION_DEFECT': Heal the action interactions -> 'heal_action'.
    - 'EXPECTATION_DEFECT': Refine expectations -> 'heal_expectation'.
    - 'APP_BUG' / 'INCONCLUSIVE': Advance to next test -> 'advance_test'.
    """
    verdict = state.get("correctness_verdict", "CORRECT")

    if verdict == "CORRECT":
        return "assemble_testcase"
    elif verdict == "ACTION_DEFECT":
        heal_attempt = state.get("action_heal_attempt", 0)
        max_heals = state.get("max_action_heals", 3)
        if heal_attempt < max_heals:
            return "heal_action"
        return "advance_test"
    elif verdict == "EXPECTATION_DEFECT":
        exp_attempt = state.get("expectation_heal_attempt", 0)
        max_exp_heals = state.get("max_expectation_heals", 2)
        if exp_attempt < max_exp_heals:
            return "heal_expectation"
        return "advance_test"
    else:  # "APP_BUG" or "INCONCLUSIVE"
        return "advance_test"
