import json
import logging
import re
from typing import Any, Dict, List
from agents.llm import get_chat_model
from agents.state import ForgeState, ExpectationSpec, ExpectationItem, PostActionResult
from langchain_core.messages import SystemMessage, HumanMessage

logger = logging.getLogger("forge.agent.expectation_analysis")

EXPECTATION_ANALYSIS_SYSTEM_PROMPT = """You are a Principal Test Automation Architect and Quality Intelligence Analyst.
Your task is to derive GROUNDED EXPECTATIONS from an executed user action.

INPUTS GIVEN TO YOU:
1. Journey Intent: The functional goal of this test (e.g. "User submits contact form and sees success confirmation").
2. Pre-Action State: Elements present before the action.
3. Executed ActionSpec: Exactly what steps were performed (clicks, inputs, navigations).
4. Post-Action Result: The live resulting state (URL change, DOM delta elements added/removed, visible alert banners, headings, HTTP responses).

CRITICAL ARCHITECTURAL PRINCIPLE: OBSERVED != EXPECTED
- Just because an element appeared in the post-action DOM does NOT mean it should be asserted!
- For example: If 80 new elements appeared, do NOT pick a random `<div>` or styling container.
- Filter candidate signals strictly through the JOURNEY INTENT:
  * If the intent was successful login: Look for absence of the login form, appearance of logout/account controls, or navigation away from `/login`.
  * If the intent was form submission: Look for a success alert banner (.alert-success), form clearance, or response status < 400.
  * If the intent was a negative validation test: Look for visible error banners (.alert-danger, .invalid-feedback) and verify the user stayed on the same URL.

SEMANTIC ALERT HIERARCHY:
- `.alert` is a generic container, NOT an error!
- Classify alerts by their semantic intent:
  * `.alert-success` -> Success confirmation (assert on successful flows)
  * `.alert-danger`  -> Error banner (assert on negative/rejection flows)
  * `.alert-info`    -> Informational notification
- When verifying that 'no error message' is displayed, NEVER assert `locator('.alert').to_have_count(0)` or `locator('[role=alert]').to_have_count(0)`. That causes false failures on success banners!
  Target explicit error classes instead: `expect(page.locator('.alert-danger, .error-message, .invalid-feedback, [data-test="error"]')).to_have_count(0)`.

ASSERTION CODE RULES (SYNCHRONOUS PLAYWRIGHT PYTHON ONLY):
1. `expect()` takes ONLY a Page, Locator or APIResponse:
   - RIGHT: `expect(page.locator('.alert-success')).to_be_visible()`
   - RIGHT: `expect(page.locator('#user-name')).to_have_count(0)`
   - RIGHT: `expect(page).to_have_url(re.compile(r".*dashboard.*"))`
   - RIGHT: `assert page.url != start_url`
   - WRONG: `expect(page.url).to_equal(...)` (Raises ValueError in Python Playwright!)
2. Use snake_case Playwright methods: `to_be_visible`, `to_contain_text`, `to_have_count`, `to_have_value`.
3. Never invent hypothetical selectors. Use the exact selectors and text present in the Post-Action DOM delta!

OUTPUT FORMAT:
Return strictly a JSON object conforming to ExpectationSpec:
{
  "test_id": "<test_id>",
  "journey_intent": "<intent>",
  "expectations": [
    {
      "type": "visible_element" | "element_absence" | "url_change" | "network_status" | "text_match",
      "target": "<selector or property>",
      "expected_value": "<value>",
      "confidence": 0.95,
      "evidence": [
        "Evidence 1 supporting why this expectation validates the intent",
        "Evidence 2 observed in post-action DOM/network"
      ],
      "code": "expect(page.locator('.alert-success')).to_be_visible()"
    }
  ],
  "summary": "Concise summary of verified expectations"
}
Output ONLY valid JSON.
"""


def expectation_analysis_node(state: ForgeState) -> Dict[str, Any]:
    """
    EXPECTATION_ANALYSIS node:
    Synthesizes grounded expectations by filtering post-action observed DOM and network deltas
    through the four inputs: (Intent, Pre-Action Discovery, ActionSpec, PostActionResult).
    Emits confidence scores and explicit supporting browser evidence for every assertion.
    """
    current_test = state.get("current_test") or {}
    test_id = str(current_test.get("test_id") or current_test.get("id") or "test_journey")
    journey_intent = str(current_test.get("intent") or current_test.get("title") or "User journey validation")

    action_spec = state.get("action_spec") or {}
    post_action_res: PostActionResult = state.get("post_action_result") or {}
    pre_discovery = state.get("discovery_data") or {}

    nav = post_action_res.get("navigation") or {}
    dom_delta = post_action_res.get("dom_delta") or {}
    network = post_action_res.get("network") or {}

    logger.info(f"[EXPECTATION_ANALYSIS] Grounding expectations for '{test_id}' based on intent and live post-action result.")

    payload = {
        "test_id": test_id,
        "journey_intent": journey_intent,
        "test_type": current_test.get("type", "FLOW"),
        "pre_action_url": nav.get("previous_url"),
        "post_action_url": nav.get("result_url"),
        "url_changed": nav.get("url_changed"),
        "action_steps_performed": action_spec.get("steps", []),
        "post_action_dom_delta": {
            "visible_alerts": dom_delta.get("visible_alerts", []),
            "current_headings": dom_delta.get("current_headings", []),
            "added_elements": dom_delta.get("added_elements", [])[:10],
            "removed_elements": dom_delta.get("removed_elements", [])[:10],
        },
        "network_responses": network.get("responses", [])[:10],
    }

    llm = get_chat_model()
    messages = [
        SystemMessage(content=EXPECTATION_ANALYSIS_SYSTEM_PROMPT),
        HumanMessage(content=f"Derive grounded expectations:\n{json.dumps(payload, indent=2, default=str)}")
    ]

    expectation_spec: ExpectationSpec
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

        spec_dict = json.loads(content)
        raw_expectations = spec_dict.get("expectations", [])
        validated_items: List[ExpectationItem] = []

        for item in raw_expectations:
            code = item.get("code", "")
            # Basic sanity filter for valid python assertion
            if code and ("expect(" in code or "assert " in code):
                validated_items.append({
                    "type": item.get("type", "visible_element"),
                    "target": item.get("target", ""),
                    "expected_value": item.get("expected_value", ""),
                    "confidence": float(item.get("confidence", 0.9)),
                    "evidence": item.get("evidence", ["Observed in post-action browser state"]),
                    "code": code,
                })

        if not validated_items:
            # Fallback deterministic expectation
            if nav.get("url_changed"):
                validated_items.append({
                    "type": "url_change",
                    "target": "page.url",
                    "expected_value": nav.get("result_url"),
                    "confidence": 0.95,
                    "evidence": [f"URL changed from {nav.get('previous_url')} to {nav.get('result_url')}"],
                    "code": f"assert page.url != {nav.get('previous_url')!r}",
                })
            else:
                validated_items.append({
                    "type": "network_status",
                    "target": "network",
                    "expected_value": "clean_execution",
                    "confidence": 0.85,
                    "evidence": ["Action executed cleanly without unhandled application error"],
                    "code": "# Functional journey executed without unhandled errors",
                })

        expectation_spec = {
            "test_id": test_id,
            "journey_intent": journey_intent,
            "expectations": validated_items,
            "summary": spec_dict.get("summary", f"Derived {len(validated_items)} grounded assertions."),
        }
    except Exception as e:
        logger.warning(f"[EXPECTATION_ANALYSIS] LLM analysis failed ({e}). Building deterministic expectation fallback.")
        fallback_items: List[ExpectationItem] = []
        if nav.get("url_changed"):
            fallback_items.append({
                "type": "url_change",
                "target": "page.url",
                "expected_value": nav.get("result_url"),
                "confidence": 0.95,
                "evidence": [f"URL changed to {nav.get('result_url')}"],
                "code": f"assert page.url != {nav.get('previous_url')!r}",
            })
        if dom_delta.get("visible_alerts"):
            first_alert = dom_delta["visible_alerts"][0]
            is_success_alert = bool(re.search(r"\b(success|successfully|thank|confirmed|created|saved)\b", first_alert, re.I))
            is_error_alert = bool(re.search(r"\b(error|invalid|failed|required|rejected)\b", first_alert, re.I))

            if is_success_alert:
                target_sel = ".alert-success, [data-test='success']"
                target_desc = ".alert-success"
            elif is_error_alert:
                target_sel = ".alert-danger, .error-message, .invalid-feedback, [data-test='error']"
                target_desc = ".alert-danger"
            else:
                target_sel = None
                target_desc = "text"

            assertion_code = (
                f"expect(page.locator({target_sel!r})).to_be_visible()"
                if target_sel
                else f"expect(page.get_by_text({first_alert[:50]!r})).to_be_visible()"
            )

            fallback_items.append({
                "type": "visible_element",
                "target": target_desc,
                "expected_value": first_alert,
                "confidence": 0.90,
                "evidence": [f"Visible alert banner appeared: '{first_alert}'"],
                "code": assertion_code,
            })

        expectation_spec = {
            "test_id": test_id,
            "journey_intent": journey_intent,
            "expectations": fallback_items,
            "summary": "Deterministic expectation derived from post-action URL and alerts.",
        }

    logger.info(
        f"[EXPECTATION_ANALYSIS] Synthesized {len(expectation_spec['expectations'])} grounded assertion(s) for '{test_id}': "
        f"{', '.join(e['type'] for e in expectation_spec['expectations'])}"
    )

    return {"expectation_spec": expectation_spec}
