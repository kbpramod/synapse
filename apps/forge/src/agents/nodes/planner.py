import json
import logging
from typing import Any, Dict, List
from agents.llm import get_chat_model
from agents.state import ForgeState, UserJourney
from storage.local import save_hypotheses
from langchain_core.messages import SystemMessage, HumanMessage

logger = logging.getLogger("forge.agent.planner")

PLANNER_SYSTEM_PROMPT = """You are a Senior Test Architect specializing in User Journey Validation.
Your task is to synthesize high-value test hypotheses to validate this web application based on the discovered page model and understanding.

TEST CATEGORIZATION:
You MUST divide your test hypotheses into two distinct categories:
1. "SMOKE" (1 to 2 tests):
   - Fast sanity checks validating that the target page loads cleanly, primary navigation is functional, and core entry points / modals open without JavaScript crashes.
   - Example: Verify landing page loads and clicking primary navigation / contact link successfully transitions state.
2. "FLOW" (4 to 7 tests):
   - Multi-step end-to-end user journeys validating real capabilities:
     ACTION -> STATE TRANSITION -> OUTCOME
   - Example: Complete form submission, user authentication, search and filter flow, or multi-step checkout/interaction.

NEGATIVE AND BOUNDARY COVERAGE (REQUIRED):
Never stop at the happy path. For EVERY form or authentication surface you find (login,
signup, search, checkout, contact), plan the successful journey AND a set of failure and
boundary journeys alongside it. For a login form, that means separate test hypotheses for:
   - valid credentials succeed (happy path)
   - wrong password with a valid username is rejected
   - unknown / wrong email or username is rejected
   - malformed email format is rejected (e.g. "not-an-email")
   - excessively long email / username input is handled without crashing (e.g. 300+ chars)
   - excessively long password input is handled without crashing (e.g. 300+ chars)
   - empty submission with both fields blank is rejected
Apply the same thinking to other forms: invalid values, wrong types, over-length input,
required fields left empty, and boundary values.

For every negative test, `expected` must assert the application FAILS GRACEFULLY:
an inline validation or error message is shown, the user stays on the same page, and the
app does NOT navigate to an authenticated state or throw an unhandled error. A negative
test PASSES when the application correctly rejects the bad input.

Use ids that make the case obvious, e.g. "flow_login_valid", "flow_login_wrong_password",
"flow_login_invalid_email_format", "flow_login_overlong_password".

BUILD EXPECTATIONS FROM `assertable_signals`, NOT FROM IMAGINATION (CRITICAL):
The context includes `assertable_signals`, derived directly from what the browser actually
observed on this page. Treat it as the source of truth for every `expected` entry:
  - `assertable_now`        — facts true on the current page; safe to assert directly.
  - `state_change_signals`  — elements present BEFORE the action whose DISAPPEARANCE proves a
                              successful transition. This is the preferred success assertion.
  - `known_routes`          — the ONLY routes that exist. Never assert a path outside this list.
  - `request_endpoints`     — real form actions; assert the submit response status instead of
                              guessing where the browser lands.
  - `unverified`            — things that genuinely cannot be known from this snapshot. Never
                              write an expectation that depends on any of these.
  - `recommended_success_assertions` / `recommended_failure_assertions` — ready-made, grounded
                              assertions; prefer them over anything you would invent.

If `assertable_signals` does not support the outcome you want to assert, that outcome is not
verifiable — assert a weaker signal that IS supported rather than inventing one.

NEVER INVENT DESTINATION ROUTES (CRITICAL):
You do NOT know where the application navigates after an action. Do not guess a URL path.
PROHIBITED: "user is redirected to /dashboard", "navigation:/dashboard", "lands on /home",
"URL becomes /account" — unless that exact path appears in the provided available_links.
Inventing a route produces a test that fails against a perfectly healthy application and
gets misreported as an application bug.

Instead, express a successful transition with signals that are observable without knowing
the destination. Prefer, in this order:
   1. The pre-action state is GONE — e.g. the login form / password field is no longer
      visible, the submit button is gone, the modal closed.
   2. An authenticated/success indicator APPEARS — e.g. a logout control, user/account menu,
      a confirmation message, or content only reachable after the action.
   3. The URL simply CHANGED from the starting URL (assert "differs from the login URL",
      never a specific path).
   4. The underlying request SUCCEEDED — e.g. the form's submit/auth network response
      returned a non-error HTTP status, and no 4xx/5xx or console error was produced.
Only assert a concrete path when that route is present in available_links.

CRITICAL RULE:
Do NOT generate tests that merely check if a static element exists.
PROHIBITED: "Verify Contact Us button exists", "Verify Login button is visible", "Verify heading is present".
REQUIRED: Real user interactions that test state transitions and functional outcomes.

For each test hypothesis, provide strictly:
- id: Descriptive slug prefixed with type, e.g. "smoke_primary_navigation", "flow_login", "flow_contact_submission"
- type: Exactly "SMOKE" or "FLOW"
- intent: Clear intent of the user/system (e.g. "A user can log into the application")
- preconditions: List of prerequisites (e.g. ["valid credentials are available", "homepage is loaded"])
- steps: Action sequence to execute the journey
- expected: List of expected outcomes/assertions (e.g. ["user reaches authenticated application state"])
- evidence: Grounding evidence ACTUALLY OBSERVED in the discovery data — element names/text
  from the discovered elements, and routes only if that exact href appears in available_links
  (e.g. ["element:email", "element:password", "element:login_button"])
- supported_viewports: ["desktop", "tablet", "mobile"]
- priority: "high", "medium", or "low"

Return strictly a JSON array of objects matching this schema:
[
  {
    "id": "smoke_navigation_header",
    "type": "SMOKE",
    "intent": "Verify page loads and main navigation responds cleanly",
    "preconditions": ["Homepage is loaded in browser"],
    "steps": [
      "Navigate to target URL",
      "Interact with primary navigation menu",
      "Verify navigation destination responds without application crash"
    ],
    "expected": [
      "Target view transitions cleanly without runtime console errors",
      "The page URL changes from the starting URL, or new content becomes visible"
    ],
    "evidence": [
      "element:nav_bar",
      "element:nav_link"
    ],
    "supported_viewports": ["desktop", "tablet", "mobile"],
    "priority": "high"
  },
  {
    "id": "flow_login_valid",
    "type": "FLOW",
    "intent": "A user can log into the application with valid credentials",
    "preconditions": [
      "valid credentials are available"
    ],
    "steps": [
      "enter email",
      "enter password",
      "submit login"
    ],
    "expected": [
      "the login form is no longer visible",
      "an authenticated indicator (e.g. logout control or account menu) is present",
      "the URL differs from the login page URL",
      "no error message and no 4xx/5xx response is produced"
    ],
    "evidence": [
      "element:email",
      "element:password",
      "element:login_button"
    ],
    "supported_viewports": ["desktop", "tablet", "mobile"],
    "priority": "high"
  },
  {
    "id": "flow_login_wrong_password",
    "type": "FLOW",
    "intent": "Login is rejected when a valid username is used with an incorrect password",
    "preconditions": [
      "a valid username is known",
      "login page is loaded"
    ],
    "steps": [
      "enter a valid username",
      "enter a deliberately incorrect password",
      "submit login"
    ],
    "expected": [
      "an error message is displayed",
      "the user remains on the login page and is NOT authenticated"
    ],
    "evidence": [
      "element:email",
      "element:password",
      "element:login_button"
    ],
    "supported_viewports": ["desktop", "tablet", "mobile"],
    "priority": "high"
  },
  {
    "id": "flow_login_overlong_password",
    "type": "FLOW",
    "intent": "An excessively long password is handled gracefully without crashing the app",
    "preconditions": [
      "login page is loaded"
    ],
    "steps": [
      "enter a valid username",
      "enter a 300+ character password",
      "submit login"
    ],
    "expected": [
      "the application rejects the attempt with a validation or error message",
      "no unhandled exception or blank error page is produced"
    ],
    "evidence": [
      "element:email",
      "element:password",
      "element:login_button"
    ],
    "supported_viewports": ["desktop"],
    "priority": "medium"
  }
]
Output ONLY valid JSON.
"""


def planner_node(state: ForgeState) -> Dict[str, Any]:
    """
    JOURNEY PLANNER node:
    Transforms discovered capabilities into structured test hypotheses divided into SMOKE and FLOW tests.
    Saves hypotheses to <storage_root>/<domain>/planner/hypotheses.json and category folders.
    """
    disc = state.get("discovery_data") or {}
    page_info = disc.get("page", {})
    target_url = page_info.get("url") or state.get("target_url", "")
    understanding = state.get("page_understanding") or {}
    elements = disc.get("elements", {})
    vp_summary = elements.get("viewports_summary", {})
    config_viewport = (state.get("config", {}).get("viewport") or "desktop").lower()

    logger.info(f"[JOURNEY PLANNER] Formulating SMOKE and FLOW hypotheses for: {target_url}")

    # Usernames/roles of the real registered accounts (never passwords — the planner only
    # writes hypotheses; the builder injects actual credentials into the script). Knowing a
    # valid username exists lets it plan cases like "valid username + wrong password".
    try:
        from db.repository import ForgeRepository
        scoped_website_id = state.get("website_id")
        accounts = (
            ForgeRepository.get_credentials_for_website(int(scoped_website_id))
            if scoped_website_id
            else ForgeRepository.get_credentials_for_url(target_url)
        )
        known_accounts = [{"username": a.get("username"), "role": a.get("role")} for a in accounts]
    except Exception as acc_err:
        logger.warning(f"[JOURNEY PLANNER] Could not load accounts for {target_url}: {acc_err}")
        known_accounts = []

    planner_input = {
        "url": target_url,
        "known_accounts": known_accounts,
        # Grounded assertion catalogue derived from real discovery output, plus an explicit
        # statement of what a page snapshot cannot know (see nodes/expectation.py).
        "assertable_signals": state.get("assertable_signals") or {},
        "title": page_info.get("title"),
        "page_type": understanding.get("page_type"),
        "purpose": understanding.get("purpose"),
        "capabilities": understanding.get("capabilities", []),
        "state_transitions": understanding.get("state_transitions", []),
        "primary_actions": understanding.get("primary_actions", []),
        "key_elements": understanding.get("key_interactive_elements", []),
        "viewports_summary": vp_summary,
        "available_buttons": [
            {"text": b.get("text"), "visible_viewports": b.get("visible_viewports", ["desktop"])}
            for b in elements.get("buttons", [])[:12]
        ],
        "available_links": [
            {"text": l.get("text"), "href": l.get("href"), "visible_viewports": l.get("visible_viewports", ["desktop"])}
            for l in elements.get("links", [])[:12]
        ],
        "available_inputs": [
            {
                "placeholder": i.get("placeholder"),
                "name": i.get("name"),
                "type": i.get("type"),
                # Observed initial state for checkbox/radio inputs — never assume the opposite.
                "checked_at_load": i.get("checked"),
            }
            for i in elements.get("inputs", [])[:8]
        ],
    }

    try:
        llm = get_chat_model()
        messages = [
            SystemMessage(content=PLANNER_SYSTEM_PROMPT),
            HumanMessage(content=f"Discovered Capabilities & Structure:\n{json.dumps(planner_input, indent=2, default=str)}")
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

        test_plan: List[UserJourney] = []
        for item in raw_plan:
            # Normalize and enforce schema
            test_type = str(item.get("type") or "FLOW").strip().upper()
            if test_type not in ("SMOKE", "FLOW"):
                test_type = "SMOKE" if "smoke" in str(item.get("id", "")).lower() else "FLOW"
            item["type"] = test_type

            # Normalize intent / goal / title / name
            intent = item.get("intent") or item.get("goal") or item.get("description") or item.get("name") or "User Journey"
            item["intent"] = intent
            item["goal"] = intent
            item["name"] = item.get("name") or intent
            item["title"] = item.get("title") or item["name"]
            item["description"] = item.get("description") or intent

            # Normalize expected
            raw_expected = item.get("expected")
            if isinstance(raw_expected, list):
                expected_list = [str(e) for e in raw_expected]
            elif isinstance(raw_expected, str):
                expected_list = [raw_expected]
            else:
                expected_list = [item.get("expected_outcome", "State transition succeeds cleanly")]
            item["expected"] = expected_list
            item["expected_outcome"] = "; ".join(expected_list)

            # Normalize evidence
            raw_evidence = item.get("evidence")
            if isinstance(raw_evidence, list):
                item["evidence"] = [str(ev) for ev in raw_evidence]
            else:
                item["evidence"] = []

            # Preconditions & steps
            item["preconditions"] = item.get("preconditions") or ["Target page is loaded"]
            item["steps"] = item.get("steps") or ["Navigate to target URL", "Perform interaction", "Verify expected state"]

            # Viewports & category
            item["supported_viewports"] = item.get("supported_viewports") or ["desktop", "tablet", "mobile"]
            if "viewport" not in item or not item["viewport"]:
                item["viewport"] = config_viewport
            item["category"] = item.get("category") or test_type.lower()
            item["priority"] = item.get("priority") or ("high" if test_type == "SMOKE" else "medium")

            test_plan.append(item)

    except Exception as e:
        logger.warning(f"[JOURNEY PLANNER] LLM planner failed ({e}). Generating fallback SMOKE and FLOW hypotheses.")
        page_title = page_info.get("title") or "Target Page"
        test_plan = [
            {
                "id": "smoke_primary_navigation",
                "type": "SMOKE",
                "intent": f"Verify {page_title} loads and primary navigation is responsive",
                "name": f"Smoke Test: {page_title} Navigation",
                "title": f"Smoke Test: {page_title} Navigation",
                "goal": f"Verify {page_title} loads and primary navigation is responsive",
                "description": f"Smoke check verifying page loads and primary navigational elements respond",
                "priority": "high",
                "category": "smoke",
                "preconditions": ["Browser opens target page"],
                "steps": [
                    f"Navigate to {target_url}",
                    "Check page loads without fatal runtime errors",
                    "Interact with primary navigation element"
                ],
                "expected": ["Page renders successfully and navigation transitions cleanly"],
                "expected_outcome": "Page renders successfully and navigation transitions cleanly",
                "evidence": ["element:navbar", "navigation:/"],
                "supported_viewports": ["desktop", "tablet", "mobile"],
                "viewport": config_viewport,
            },
            {
                "id": "flow_core_interaction",
                "type": "FLOW",
                "intent": f"Validate core capability workflow for {page_title}",
                "name": f"Core Journey Flow: {page_title}",
                "title": f"Core Journey Flow: {page_title}",
                "goal": f"Validate core capability workflow for {page_title}",
                "description": f"End-to-end journey validating capability state transitions",
                "priority": "high",
                "category": "flow",
                "preconditions": ["Browser opens target page"],
                "steps": [
                    f"Navigate to {target_url}",
                    "Perform core interaction sequence",
                    "Verify target state transition is reached"
                ],
                "expected": ["Application reaches expected interactive state transition without crash"],
                "expected_outcome": "Application reaches expected interactive state transition without crash",
                "evidence": ["element:button", "element:input"],
                "supported_viewports": ["desktop", "tablet", "mobile"],
                "viewport": config_viewport,
            }
        ]

    # Persist hypotheses to storage folder
    if target_url:
        try:
            saved_path = save_hypotheses(target_url, test_plan)
            logger.info(f"[JOURNEY PLANNER] Hypotheses persisted to: {saved_path}")
        except Exception as save_err:
            logger.warning(f"[JOURNEY PLANNER] Could not save hypotheses to storage: {save_err}")

    smoke_count = sum(1 for t in test_plan if t.get("type") == "SMOKE")
    flow_count = sum(1 for t in test_plan if t.get("type") == "FLOW")
    logger.info(
        f"[JOURNEY PLANNER] Planned {len(test_plan)} total hypotheses "
        f"({smoke_count} SMOKE, {flow_count} FLOW). First: '{test_plan[0]['id']}' [{test_plan[0].get('type')}]"
    )

    return {
        "test_plan": test_plan,
        "current_test_idx": 0,
        "current_test": test_plan[0],
        "heal_attempt": 0,
        "healing_history": [],
    }
