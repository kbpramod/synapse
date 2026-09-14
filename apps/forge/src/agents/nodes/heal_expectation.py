import json
import logging
from typing import Any, Dict, List
from agents.llm import get_chat_model
from agents.state import ForgeState, ExpectationSpec, ExpectationItem, PostActionResult
from langchain_core.messages import SystemMessage, HumanMessage

logger = logging.getLogger("forge.agent.heal_expectation")

HEAL_EXPECTATION_SYSTEM_PROMPT = """You are an elite Quality Intelligence Expectation Healer.
A user journey executed properly, but the initial expectation criteria were misaligned with the legitimate application state transition.

CRITICAL ARCHITECTURAL CONSTRAINTS:
1. EXPECTATIONS ONLY — ACTION CODE IS STRICTLY IMMUTABLE:
   - You do NOT have access to action code or interaction steps.
   - You must NOT modify how the test interacts with the application.
   - You are ONLY refining the validation assertions to align with the legitimate observed post-action outcome.

2. PREVENT ASSERTION EROSION:
   - Do NOT weaken an assertion simply to make a broken application look passing.
   - The refined assertion must still genuinely prove the Journey Intent.
   - For example: If the intent was "User sees contact confirmation", and the app displayed a heading "Get in touch" instead of "Contact Us", you may update the heading text matcher.
   - However, if the page shows an unexpected error banner, that is NOT an expectation defect.

3. VALID PLAYWRIGHT PYTHON ASSERTIONS ONLY:
   - `expect(page.locator(...)).to_be_visible()`
   - `expect(page.locator(...)).to_have_text(...)`
   - `expect(page.locator(...)).to_have_count(0)`
   - `assert page.url != start_url`

OUTPUT FORMAT:
Return strictly a JSON object:
{
  "test_id": "<test_id>",
  "diagnosis": "Why the original expectation was misaligned and how it is being refined",
  "expectations": [
    {
      "type": "visible_element" | "element_absence" | "url_change" | "text_match",
      "target": "<selector or property>",
      "expected_value": "<value>",
      "confidence": 0.95,
      "evidence": ["Direct observation in post-action DOM"],
      "code": "expect(page.locator(...)).to_be_visible()"
    }
  ],
  "summary": "Concise summary of refined expectations"
}
Output ONLY valid JSON.
"""


def heal_expectation_node(state: ForgeState) -> Dict[str, Any]:
    """
    HEAL_EXPECTATION node:
    Structurally isolated expectation healer.
    Receives only ExpectationSpec and PostActionResult. Action code is immutable.
    Enforces max_expectation_heals budget to prevent infinite assertion-erosion loops.
    """
    exp_spec = state.get("expectation_spec") or {}
    test_id = exp_spec.get("test_id", "test_journey")
    intent = exp_spec.get("journey_intent", "")
    heal_attempt = state.get("expectation_heal_attempt", 0) + 1

    post_action_res: PostActionResult = state.get("post_action_result") or {}
    dom_delta = post_action_res.get("dom_delta") or {}
    nav = post_action_res.get("navigation") or {}
    correctness_reason = state.get("correctness_reason") or ""

    logger.info(f"[HEAL_EXPECTATION] Executing expectation refinement #{heal_attempt} for '{test_id}'")

    payload = {
        "test_id": test_id,
        "journey_intent": intent,
        "existing_expectations": exp_spec.get("expectations", []),
        "discrepancy_reason": correctness_reason,
        "post_action_url": nav.get("result_url"),
        "current_headings": dom_delta.get("current_headings", []),
        "visible_alerts": dom_delta.get("visible_alerts", []),
        "added_elements": dom_delta.get("added_elements", [])[:10],
    }

    llm = get_chat_model()
    messages = [
        SystemMessage(content=HEAL_EXPECTATION_SYSTEM_PROMPT),
        HumanMessage(content=f"Refine the expectation criteria:\n{json.dumps(payload, indent=2, default=str)}")
    ]

    repaired_spec = exp_spec
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

        repaired_dict = json.loads(content)
        new_items = repaired_dict.get("expectations", [])
        if new_items:
            repaired_spec = {
                "test_id": test_id,
                "journey_intent": intent,
                "expectations": new_items,
                "summary": repaired_dict.get("summary", "Expectations refined from post-action evidence."),
            }
            logger.info(f"[HEAL_EXPECTATION] Refined {len(new_items)} expectation(s). Diagnosis: {repaired_dict.get('diagnosis')}")
    except Exception as e:
        logger.warning(f"[HEAL_EXPECTATION] Refinement failed ({e}). Keeping existing expectations.")

    return {
        "expectation_spec": repaired_spec,
        "expectation_heal_attempt": heal_attempt,
    }
