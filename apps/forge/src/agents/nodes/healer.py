import json
import logging
from pathlib import Path
from typing import Any, Dict
from agents.llm import get_chat_model
from agents.state import ForgeState, HealEvent
from langchain_core.messages import SystemMessage, HumanMessage

logger = logging.getLogger("forge.agent.healer")

HEALER_SYSTEM_PROMPT = """You are a Self-Healing Test Automation Specialist.

A Playwright automated user journey test has failed. Your task is to diagnose the failure and
produce a concrete, tactical repair plan that can be applied to the test script by the Editor.

STRICT CONSTRAINTS & RULES:
1. SYNCHRONOUS API ONLY:
   The test suite strictly uses the Playwright Python Synchronous API (`from playwright.sync_api import sync_playwright, expect`).
   NEVER introduce `async`, `await`, or asyncio into the repair plan! All Playwright calls must remain synchronous.

2. RESPONSIVE VARIANT & HIDDEN ELEMENTS:
   If an element is reported hidden (e.g. 'locator resolved to hidden element'), check if this is a responsive layout difference.
   For example, if testing mobile viewport and the target link is in a collapsed menu, plan to click the visible mobile menu toggle first, or use the locator corresponding to the visible variant.

3. GROUNDED LOCATORS & ACTIONS:
   Inspect the Discovered DOM elements provided. Use actual selectors, roles, or text found in the application.
   Do not automatically suggest increasing waits unless there is clear evidence of an asynchronous race condition.

4. THE EXPECTATION ITSELF MAY BE WRONG — CHECK THIS FIRST:
   These tests are generated from hypotheses, so the assertion may be asserting something the
   application never does. Before proposing any wait/selector/timeout change, ask:
   "Does the thing being asserted actually EXIST in this application?"

   Search the Discovered DOM elements for the asserted element/text/role:
   - If it is NOT present anywhere in the discovered elements, the expectation is WRONG.
     Adding a wait for an element that does not exist only turns a fast failure into a slow
     one, and after the heal budget is exhausted the healthy application gets misreported as
     having a bug. The correct repair is to REPLACE OR REMOVE THE ASSERTION.
   - Example: the test asserts a "Logout" button is visible after login, but no logout button
     exists in the discovered DOM. Do NOT plan `page.wait_for_selector('button:has-text("Logout")')`.
     Instead plan to assert something that genuinely marks the post-login state, chosen from
     what was actually discovered — e.g. the login form/password field is gone, an element
     only present when authenticated is visible, the URL differs from the starting URL, or the
     submit response returned a non-error status.
   - Likewise, never repair an invented destination route (e.g. "expected URL .*dashboard.*")
     by tweaking the regex. Remove that assertion and use a destination-independent signal.

   If a PREVIOUS heal attempt already tried waits/selector changes for the same element and it
   still failed, stop retrying that path — treat it as a wrong expectation and fix the assertion.

The failure may be caused by:
- an incorrect expectation: asserting an element, text, or route the application does not have
- locator mismatch or target element hidden under current viewport
- missing interaction sequence (e.g. need to open menu or click parent container)
- generated test code errors (e.g. NameError, missing import, incorrect function call)
- timing/wait condition mismatch

Inputs provided:
- Error message & stack trace
- Current test code
- The test's intent and its expected outcomes (the hypothesis being validated)
- Analyzer diagnosis
- Discovered DOM elements & viewports
- Previous heal attempts

Generate a specific tactical repair plan.

1. Failure Class:
   Exactly one of:
   - "wrong_expectation": the test asserts something this application does not do/have.
     The assertion must be replaced or removed.
   - "automation_defect": the expectation is valid and observable, but the script reaches it
     incorrectly (bad selector, missing step, real race condition, code error).

2. Diagnosis:
   Identify the most likely root cause using the actual error, the test code, and whether the
   asserted target appears in the discovered elements.

3. Fix Plan:
   Describe exactly what the Editor should change. When failure_class is "wrong_expectation",
   state explicitly which assertion to delete and what grounded assertion replaces it.

4. Preserve:
   Identify existing test behavior that should not be changed. Never list a broken assertion
   as something to preserve.

Return strictly JSON:
{
  "failure_class": "wrong_expectation" | "automation_defect",
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
        "buttons": [
            {"text": b.get("text"), "selector": b.get("selector"), "id": b.get("id")}
            for b in (disc.get("elements", {}).get("buttons", []))[:20]
        ],
        "links": [
            {"text": l.get("text"), "selector": l.get("selector"), "id": l.get("id"), "href": l.get("href")}
            for l in (disc.get("elements", {}).get("links", []))[:25]
        ],
        "inputs": [
            {"name": i.get("name"), "placeholder": i.get("placeholder"), "selector": i.get("selector")}
            for i in (disc.get("elements", {}).get("inputs", []))[:15]
        ]
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
        # The hypothesis being validated — needed to judge whether the expectation itself
        # is sound, not just whether the script reaches it correctly.
        "test_intent": current_test.get("intent") or current_test.get("goal") or current_test.get("description"),
        "expected_outcomes": current_test.get("expected") or [current_test.get("expected_outcome")],
        "error_summary": exec_res.get("error_summary"),
        "stderr": (exec_res.get("stderr") or "")[-2000:],
        "suggested_analysis": analysis.get("suggested_fix"),
        "available_elements_in_dom": available_elements,
        "previous_heal_attempts": healing_history[-3:],
        "test_code": code_to_heal[-2000:]
    }

    try:
        llm = get_chat_model()
        messages = [
            SystemMessage(content=HEALER_SYSTEM_PROMPT),
            HumanMessage(content=f"Failure Context for Healing:\n{json.dumps(healer_payload, indent=2, default=str)}")
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
        failure_class = str(heal_dict.get("failure_class") or "automation_defect").strip().lower()
        if failure_class not in ("wrong_expectation", "automation_defect"):
            failure_class = "automation_defect"
    except Exception as e:
        logger.warning(f"[HEAL] LLM healer failed ({e}). Using fallback heuristic repair plan.")
        diagnosis = f"Encountered {exec_res.get('error_summary') or 'Timeout failure'}."
        fix_plan = "Add wait_for_load_state('networkidle') and use get_by_role / text locators with generous timeouts."
        preserve = "Keep all existing setup and working assertions."
        failure_class = "automation_defect"

    heal_event: HealEvent = {
        "attempt": heal_attempt + 1,
        "test_id": current_test.get("id", "unknown"),
        "error_snippet": exec_res.get("error_summary", "")[:200],
        "failure_class": failure_class,
        "diagnosis": diagnosis,
        "fix_plan": fix_plan,
        "preserve": preserve,
    }
    healing_history.append(heal_event)

    logger.info(f"[HEAL] Failure Class: {failure_class}")
    logger.info(f"[HEAL] Diagnosis: {diagnosis}")
    logger.info(f"[HEAL] Fix Plan: {fix_plan}")
    logger.info(f"[HEAL] Preserve: {preserve}")

    return {
        "heal_attempt": heal_attempt + 1,
        "healing_history": healing_history,
        "healing_plan": {
            "failure_class": failure_class,
            "diagnosis": diagnosis,
            "fix_plan": fix_plan,
            "preserve": preserve,
        },
    }
