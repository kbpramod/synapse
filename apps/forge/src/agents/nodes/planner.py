import json
import logging
from typing import Any, Dict, List
from agents.llm import get_chat_model
from agents.state import ForgeState, TestScenario
from langchain_core.messages import SystemMessage, HumanMessage

logger = logging.getLogger("forge.agent.planner")

PLANNER_SYSTEM_PROMPT = """You are a Senior QA Automation Architect.
Given the page understanding and interactive DOM elements of a web page, design a prioritized suite of automated end-to-end tests to validate this page using Playwright.

Formulate 2 to 4 high-value test scenarios covering:
1. Smoke / Critical Visibility: Key headings, core CTA buttons, and basic page health.
2. Primary Interactive Flow: Filling out active forms, clicking key navigational buttons, or triggering search.
3. Edge Case / Input Validation: Submitting invalid/empty forms or asserting error messages where applicable.

For each test scenario, provide:
- id: A slug like 'test_smoke_page_load' or 'test_submit_contact_form'
- title: Human-readable name
- description: Purpose of the test
- priority: 'high', 'medium', or 'low'
- category: 'smoke', 'happy_path', 'validation', or 'navigation'
- steps: Step-by-step actions (e.g., ["Navigate to URL", "Wait for search bar", "Type query", "Press Enter", "Assert results"])
- expected_outcome: Exact expected outcome or visual/assertion confirmation

Return strictly a JSON array of objects matching this schema:
[
  {
    "id": "test_page_smoke",
    "title": "Smoke Page Load & CTAs",
    "description": "Validates page title and presence of primary CTA buttons",
    "priority": "high",
    "category": "smoke",
    "steps": ["Go to page", "Verify title contains X", "Verify button Y is visible"],
    "expected_outcome": "Page loads without console errors and CTA is visible"
  }
]
Output ONLY the JSON array.
"""


def planner_node(state: ForgeState) -> Dict[str, Any]:
    """
    TEST PLANNER node: Decides 'What should I test?' based on discovery and understanding.
    Produces a prioritized test suite.
    """
    disc = state.get("discovery_data") or {}
    page_info = disc.get("page", {})
    understanding = state.get("page_understanding") or {}

    logger.info(f"[TEST PLANNER] Formulating test plan for: {page_info.get('url')}")

    planner_input = {
        "url": page_info.get("url"),
        "title": page_info.get("title"),
        "page_type": understanding.get("page_type"),
        "purpose": understanding.get("purpose"),
        "primary_actions": understanding.get("primary_actions"),
        "key_elements": understanding.get("key_interactive_elements"),
        "available_buttons": [b.get("text") for b in (disc.get("elements", {}).get("buttons", []))[:10]],
        "available_inputs": [i.get("placeholder") or i.get("name") for i in (disc.get("elements", {}).get("inputs", []))[:10]]
    }

    try:
        llm = get_chat_model()
        messages = [
            SystemMessage(content=PLANNER_SYSTEM_PROMPT),
            HumanMessage(content=f"Page Context & Capabilities:\n{json.dumps(planner_input, indent=2)}")
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

        raw_plan = json.loads(content)
        if not isinstance(raw_plan, list) or len(raw_plan) == 0:
            raise ValueError("Test plan output must be a non-empty list.")
        
        test_plan: List[TestScenario] = raw_plan
    except Exception as e:
        logger.warning(f"[TEST PLANNER] LLM planner failed or produced invalid plan ({e}). Using default smoke test scenario.")
        test_plan = [
            {
                "id": "test_page_smoke",
                "title": f"Smoke verification for {page_info.get('title', 'Target Page')}",
                "description": "Asserts the page loads successfully, title is valid, and primary elements render.",
                "priority": "high",
                "category": "smoke",
                "steps": [
                    f"Navigate to {page_info.get('url')}",
                    "Wait for DOM content loaded",
                    f"Verify page title is '{page_info.get('title', '')}'"
                ],
                "expected_outcome": "Page loads cleanly with expected title"
            }
        ]

    logger.info(f"[TEST PLANNER] Created test plan with {len(test_plan)} test scenarios: {[t['id'] for t in test_plan]}")

    return {
        "test_plan": test_plan,
        "current_test_idx": 0,
        "current_test": test_plan[0] if test_plan else None,
        "heal_attempt": 0,
        "max_heal_attempts": state.get("max_heal_attempts", 3),
        "healing_history": [],
        "suite_summary": []
    }
