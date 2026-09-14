import json
import logging
from pathlib import Path
from typing import Any, Dict, List
from agents.llm import get_chat_model
from agents.state import ForgeState, ActionSpec, ActionStep
from langchain_core.messages import SystemMessage, HumanMessage
from agents.nodes.action_builder import render_action_script
from agents.script_lint import apply_lint

logger = logging.getLogger("forge.agent.heal_action")

HEAL_ACTION_SYSTEM_PROMPT = """You are an elite Self-Healing Action Automation Agent.
An action interaction sequence on a web application failed (either mechanically due to a locator/timing error, or semantically because the interaction did not trigger the expected action).

CRITICAL ARCHITECTURAL CONSTRAINTS:
1. YOU ARE REPAIRING USER ACTIONS ONLY:
   - You do NOT have access to assertions or expectations.
   - You must NOT output or suggest assertions or expectations.
   - You must ONLY repair the interaction steps (locators, values, clicks, waits, scrolls).

2. ALLOWED REPAIRS:
   - Fix element selector/locator using discovered DOM evidence.
   - Add `.scroll_into_view_if_needed()` or scroll step before interaction.
   - Add or adjust wait timing (`wait_for_timeout`, `wait_for_load_state`).
   - Fix fill values (e.g. use valid accounts or missing fields).
   - Add a missing prerequisite step (e.g. dismiss modal, open menu, accept checkbox).

3. DOM TECHNICAL EVIDENCE INVARIANT:
   - Discovered DOM locators, IDs, attributes, classes, and names are immutable technical evidence.
   - Never spell-correct or rewrite discovered IDs or selectors.
   - Selectors MUST exist in the provided discovery data.

OUTPUT FORMAT:
Return strictly a JSON object with the repaired ActionSpec:
{
  "test_id": "<test_id>",
  "target_url": "<url>",
  "diagnosis": "Concise explanation of what broke in the action and why",
  "steps": [
    {
      "step_id": 1,
      "action_type": "goto" | "click" | "fill" | "select" | "press" | "scroll" | "wait",
      "selector": "<selector or null>",
      "value": "<value or null>",
      "description": "<description>"
    }
  ]
}
Output ONLY valid JSON.
"""


def heal_action_node(state: ForgeState) -> Dict[str, Any]:
    """
    HEAL_ACTION node:
    Structurally isolated action healer that repairs ONLY the ActionSpec.
    Never receives expectation code. Recompiles the ephemeral action script
    and routes back to action_runner.
    """
    action_spec = state.get("action_spec") or {}
    test_id = action_spec.get("test_id", "test_journey")
    target_url = action_spec.get("target_url", state.get("target_url", ""))
    heal_attempt = state.get("action_heal_attempt", 0) + 1

    action_res = state.get("action_result") or {}
    exec_error = action_res.get("error_summary") or action_res.get("stderr") or ""
    failure_url = action_res.get("failure_url") or target_url
    correctness_reason = state.get("correctness_reason") or ""

    discovery_data = state.get("discovery_data") or {}
    elements = discovery_data.get("elements") or {}

    logger.info(f"[HEAL_ACTION] Executing action repair #{heal_attempt} for '{test_id}'")

    payload = {
        "test_id": test_id,
        "target_url": target_url,
        "failure_url": failure_url,
        "existing_action_steps": action_spec.get("steps", []),
        "action_error": exec_error[-1500:] if exec_error else None,
        "semantic_issue": correctness_reason if correctness_reason else None,
        "heal_attempt": heal_attempt,
        "discovered_elements": {
            "buttons": elements.get("buttons", [])[:30],
            "inputs": elements.get("inputs", [])[:30],
        }
    }

    llm = get_chat_model()
    messages = [
        SystemMessage(content=HEAL_ACTION_SYSTEM_PROMPT),
        HumanMessage(content=f"Repair the failed ActionSpec:\n{json.dumps(payload, indent=2, default=str)}")
    ]

    repaired_spec = action_spec
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
        repaired_steps = repaired_dict.get("steps", [])
        if repaired_steps:
            repaired_spec = {
                "test_id": test_id,
                "target_url": repaired_dict.get("target_url") or target_url,
                "viewport": action_spec.get("viewport", {"width": 1280, "height": 800}),
                "steps": repaired_steps,
                "storage_state_path": action_spec.get("storage_state_path"),
                "timeout_ms": action_spec.get("timeout_ms", 30000),
            }
            logger.info(f"[HEAL_ACTION] Successfully updated ActionSpec with {len(repaired_steps)} steps. Diagnosis: {repaired_dict.get('diagnosis')}")
    except Exception as e:
        logger.warning(f"[HEAL_ACTION] Action repair synthesis failed ({e}). Retaining existing spec.")

    # Re-render ephemeral action script
    action_file_path = state.get("action_file_path")
    if action_file_path:
        script_path = Path(action_file_path)
        post_json_path = script_path.parent / f"{script_path.stem}_post_discovery.json"
        screenshot_path = script_path.parent / f"{script_path.stem}_post.png"

        script_code = render_action_script(
            action_spec=repaired_spec,
            output_path=script_path,
            post_discovery_json_path=post_json_path,
            screenshot_path=screenshot_path,
        )
        linted_code = apply_lint(script_code, context=f"healed_action_{test_id}")
        with open(script_path, "w", encoding="utf-8") as f:
            f.write(linted_code)

    return {
        "action_spec": repaired_spec,
        "action_heal_attempt": heal_attempt,
        "action_result": None,
    }
