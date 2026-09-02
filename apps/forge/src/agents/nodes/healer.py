import json
import logging
from typing import Any, Dict
from agents.llm import get_chat_model
from agents.state import ForgeState, HealEvent
from langchain_core.messages import SystemMessage, HumanMessage

logger = logging.getLogger("forge.agent.healer")

HEALER_SYSTEM_PROMPT = """You are a Self-Healing Test Automation Specialist.

A Playwright test has failed. Your task is to diagnose the failure and
produce a concrete repair plan that can be given to a Test Builder.

The failure may be caused by:
- generated test code errors
- missing imports
- incorrect API usage
- locator mismatch
- timing/wait problems
- dynamic DOM state
- navigation problems
- iframe/shadow DOM issues
- incorrect assertions
- other test-automation defects

Inputs provided:
- Error message & stack trace
- Current test code
- Analyzer diagnosis
- Discovered DOM elements
- Previous heal attempts

Do not assume the failure is a locator problem.

Generate a specific tactical repair plan.

1. Diagnosis:
   Identify the most likely root cause using the actual error and test code.

2. Fix Plan:
   Describe exactly what the Test Builder should change.

3. Preserve:
   Identify existing test behavior that should not be changed.

Return strictly JSON:
{
  "diagnosis": "Detailed root-cause diagnosis",
  "fix_plan": "Specific tactical steps to repair the test",
  "preserve": "Existing behavior that must remain unchanged"
}

Output ONLY valid JSON.
"""


def healer_node(state: ForgeState) -> Dict[str, Any]:
    """
    HEAL node: Diagnoses the failure, formulates a repair plan,
    increments the heal attempt counter, and prepares context for the Test Builder.
    """
    current_test = state.get("current_test") or {}
    heal_attempt = state.get("heal_attempt", 0)
    healing_history = list(state.get("healing_history", []))
    exec_res = state.get("execution_result") or {}
    analysis = state.get("analysis") or {}
    disc = state.get("discovery_data") or {}

    logger.info(f"[HEAL] Initiating self-healing loop for '{current_test.get('id')}' (Attempt #{heal_attempt + 1})")

    # Payload for healer LLM
    available_elements = {
        "buttons": [b.get("text") for b in (disc.get("elements", {}).get("buttons", []))[:15]],
        "inputs": [i.get("placeholder") or i.get("name") or i.get("selector") for i in (disc.get("elements", {}).get("inputs", []))[:15]]
    }

    code_to_heal = state.get("test_code") or ""
    test_file_path = state.get("test_file_path")
    if not code_to_heal and test_file_path and Path(test_file_path).exists():
        try:
            with open(test_file_path, "r", encoding="utf-8") as f:
                code_to_heal = f.read()
        except Exception:
            pass

    healer_payload = {
        "test_id": current_test.get("id"),
        "error_summary": exec_res.get("error_summary"),
        "stderr": (exec_res.get("stderr") or "")[-2000:],
        "suggested_analysis": analysis.get("suggested_fix"),
        "available_elements_in_dom": available_elements,
        "test_code": code_to_heal[-2000:]
    }

    try:
        llm = get_chat_model()
        messages = [
            SystemMessage(content=HEALER_SYSTEM_PROMPT),
            HumanMessage(content=f"Failure Context for Healing:\n{json.dumps(healer_payload, indent=2)}")
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

        heal_dict = json.loads(content)
        diagnosis = heal_dict.get("diagnosis", "Element locator timed out.")
        fix_plan = heal_dict.get("fix_plan", "Use more resilient text or role locator.")
        preserve = heal_dict.get("preserve", "Keep all existing setup and working assertions.")
    except Exception as e:
        logger.warning(f"[HEAL] LLM healer failed ({e}). Using fallback heuristic repair plan.")
        diagnosis = f"Encountered {exec_res.get('error_summary') or 'Timeout failure'}."
        fix_plan = "Add wait_for_load_state('networkidle') and use get_by_role / text locators with generous timeouts."
        preserve = "Keep all existing setup and working assertions."

    heal_event: HealEvent = {
        "attempt": heal_attempt + 1,
        "test_id": current_test.get("id", "unknown"),
        "error_snippet": exec_res.get("error_summary", "")[:200],
        "diagnosis": diagnosis,
        "fix_plan": fix_plan,
        "preserve": preserve,
    }
    healing_history.append(heal_event)

    logger.info(f"[HEAL] Diagnosis: {diagnosis}")
    logger.info(f"[HEAL] Fix Plan: {fix_plan}")
    logger.info(f"[HEAL] Preserve: {preserve}")

    return {
        "heal_attempt": heal_attempt + 1,
        "healing_history": healing_history,
        "healing_plan": {
            "diagnosis": diagnosis,
            "fix_plan": fix_plan,
            "preserve": preserve,
        },
    }
