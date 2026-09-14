import json
import logging
import os
from pathlib import Path
from typing import Any, Dict, List
from agents.llm import get_chat_model
from agents.state import ForgeState, ActionSpec, ActionStep
from langchain_core.messages import SystemMessage, HumanMessage
from agents.script_lint import apply_lint
from storage.local import get_website_storage_dir
from db.repository import ForgeRepository
from schemas.discovery import FIXED_VIEWPORTS

logger = logging.getLogger("forge.agent.action_builder")

ACTION_BUILDER_SYSTEM_PROMPT = """You are an elite Playwright Python Automation Engineer.
Your task is to plan and synthesize the ACTION SEQUENCE for a User Journey on a web application.

CRITICAL ARCHITECTURAL RULES:
1. ACTIONS ONLY — ABSOLUTELY ZERO ASSERTIONS OR EXPECTATIONS:
   - Do NOT write any `expect(...)` statements.
   - Do NOT write any `assert` statements.
   - Do NOT check if an element is visible or has specific text.
   - Your ONLY job is to execute the user interactions needed for this journey (e.g. navigating, typing, clicking, selecting, pressing keys, scrolling).
   - Assertions will be derived later from the live post-action result.

2. GROUNDED TECHNICAL EVIDENCE:
   - Discovered DOM locators, IDs, attributes, classes, and names are immutable technical evidence.
   - NEVER spell-correct, normalize, or semantically rewrite discovered locators.
     * If discovery found `#susbscribe_email`, you MUST use `#susbscribe_email`.
     * If discovery found `<button id="subscribe">` with no visible text, use `#subscribe`.
   - Locator Priority Hierarchy:
     1. Exact ID or unique CSS selector: e.g. `#subscribe`, `input[name='email']`.
     2. Inputs: Use `#id` or `input[name='...']` or placeholder if verified.
     3. Buttons/Links with explicit text: Use `button:has-text('...')` or exact text locator only if visible text exists.
     4. Always scroll before clicking/filling off-screen elements.

3. REAL TEST ACCOUNTS:
   - If user accounts are provided in context, use their real credentials literally.
   - Never use placeholder values like "test@example.com" or "password123" unless no accounts exist.

4. STEP TYPES:
   Each step in your sequence must have one of these action types:
   - "goto": navigate to URL (value is url)
   - "fill": type into input (selector + value)
   - "click": click an element (selector)
   - "select": choose select option (selector + value)
   - "press": press key like 'Enter' (selector + value)
   - "scroll": scroll element into view (selector)
   - "wait": wait milliseconds or state (value is ms e.g. "1000")

OUTPUT FORMAT:
Return strictly a JSON object conforming to ActionSpec:
{
  "test_id": "<test_id>",
  "target_url": "<url>",
  "steps": [
    {
      "step_id": 1,
      "action_type": "goto",
      "selector": null,
      "value": "https://example.com",
      "description": "Navigate to homepage"
    },
    {
      "step_id": 2,
      "action_type": "fill",
      "selector": "#user-name",
      "value": "standard_user",
      "description": "Enter username"
    },
    {
      "step_id": 3,
      "action_type": "click",
      "selector": "#login-button",
      "value": null,
      "description": "Click login button"
    }
  ]
}
Output ONLY valid JSON.
"""


def render_action_script(
    action_spec: ActionSpec,
    output_path: Path,
    post_discovery_json_path: Path,
    screenshot_path: Path,
) -> str:
    """
    Renders an ActionSpec into an executable, ephemeral Playwright Python script.
    The script executes the interaction steps and captures the live post-action
    state (DOM, URL, response statuses, console errors) immediately before exiting.
    """
    test_id = action_spec.get("test_id", "action_run")
    target_url = action_spec.get("target_url", "https://example.com")
    viewport = action_spec.get("viewport", {"width": 1280, "height": 800})
    steps = action_spec.get("steps", [])
    storage_state_path = action_spec.get("storage_state_path")

    # Build step execution code lines (indented 12 spaces to fit inside try: block)
    step_lines: List[str] = []
    for step in steps:
        stype = step.get("action_type")
        sel = step.get("selector")
        val = step.get("value")
        desc = step.get("description", "")

        step_lines.append(f"            # Step {step.get('step_id', '')}: {desc}")
        if stype == "goto":
            url = val or target_url
            step_lines.append(f"            page.goto('{url}', wait_until='domcontentloaded', timeout=15000)")
        elif stype == "fill":
            step_lines.append(f"            loc = page.locator({sel!r})")
            step_lines.append("            loc.scroll_into_view_if_needed(timeout=10000)")
            step_lines.append(f"            loc.fill({val!r}, timeout=10000)")
        elif stype == "click":
            step_lines.append(f"            loc = page.locator({sel!r})")
            step_lines.append("            loc.scroll_into_view_if_needed(timeout=10000)")
            step_lines.append("            loc.click(timeout=10000)")
        elif stype == "select":
            step_lines.append(f"            page.locator({sel!r}).select_option({val!r}, timeout=10000)")
        elif stype == "press":
            if sel:
                step_lines.append(f"            page.locator({sel!r}).press({val!r}, timeout=10000)")
            else:
                step_lines.append(f"            page.keyboard.press({val!r})")
        elif stype == "scroll":
            step_lines.append(f"            page.locator({sel!r}).scroll_into_view_if_needed(timeout=10000)")
        elif stype == "wait":
            ms = int(val) if (val and str(val).isdigit()) else 1000
            step_lines.append(f"            page.wait_for_timeout({ms})")
        else:
            if sel:
                step_lines.append(f"            page.locator({sel!r}).click(timeout=10000)")

    steps_code = "\n".join(step_lines) if step_lines else f"            page.goto('{target_url}', timeout=30000)"

    storage_init = (
        f"storage_state={storage_state_path!r}"
        if (storage_state_path and Path(storage_state_path).exists())
        else ""
    )
    context_args = f"viewport={viewport}"
    if storage_init:
        context_args += f", {storage_init}"

    norm_post_json = str(post_discovery_json_path.resolve()).replace("\\", "/")
    norm_screenshot = str(screenshot_path.resolve()).replace("\\", "/")

    script_template = f'''# Ephemeral Action Execution Script for: {test_id}
import os
import sys
import json
import re
from pathlib import Path
from playwright.sync_api import sync_playwright

def run_action():
    headless = os.getenv("HEADLESS", "false").lower() in ("true", "1", "yes")
    network_responses = []
    console_errors = []

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=headless)
        context = browser.new_context({context_args})
        page = context.new_page()

        # Telemetry listeners
        def on_response(res):
            try:
                network_responses.append({{
                    "url": res.url,
                    "status": res.status,
                    "method": res.request.method,
                }})
            except Exception:
                pass

        def on_console(msg):
            if msg.type in ("error", "warning"):
                console_errors.append({{"type": msg.type, "text": msg.text}})

        def on_pageerror(err):
            console_errors.append({{"type": "uncaught_exception", "text": str(err)}})

        page.on("response", on_response)
        page.on("console", on_console)
        page.on("pageerror", on_pageerror)

        try:
            print("[ACTION_START] {test_id}")
{steps_code}

            # Settle post-action
            try:
                page.wait_for_load_state("networkidle", timeout=5000)
            except Exception:
                pass
            page.wait_for_timeout(1000)

            # Extract live post-action state
            current_url = page.url
            print(f"[ACTION_URL] {{current_url}}")

            # Capture DOM state
            dom_data = page.evaluate("""() => {{
                const isVisible = (el) => {{
                    if (!el) return false;
                    const style = window.getComputedStyle(el);
                    if (style.display === 'none' || style.visibility === 'hidden' || style.opacity === '0') return false;
                    const rect = el.getBoundingClientRect();
                    return rect.width > 0 && rect.height > 0;
                }};

                const buttons = Array.from(document.querySelectorAll('button, input[type="button"], input[type="submit"], [role="button"]'))
                    .filter(isVisible)
                    .map(el => ({{
                        tag: el.tagName.toLowerCase(),
                        id: el.id || null,
                        text: (el.innerText || el.value || '').trim().substring(0, 100),
                        selector: el.id ? '#' + el.id : (el.getAttribute('name') ? '[name="' + el.getAttribute('name') + '"]' : null),
                    }}));

                const inputs = Array.from(document.querySelectorAll('input:not([type="hidden"]), textarea, select'))
                    .filter(isVisible)
                    .map(el => ({{
                        tag: el.tagName.toLowerCase(),
                        id: el.id || null,
                        name: el.getAttribute('name') || null,
                        type: el.getAttribute('type') || el.tagName.toLowerCase(),
                        value: el.value ? el.value.substring(0, 50) : '',
                        placeholder: el.getAttribute('placeholder') || null,
                        selector: el.id ? '#' + el.id : (el.getAttribute('name') ? '[name="' + el.getAttribute('name') + '"]' : null),
                    }}));

                const headings = Array.from(document.querySelectorAll('h1, h2, h3, h4'))
                    .filter(isVisible)
                    .map(el => (el.innerText || '').trim())
                    .filter(Boolean);

                const alertSel = "[role='alert'], .alert, .alert-danger, .alert-success, .alert-warning, .error-message, .invalid-feedback, [data-test='error'], .toast";
                const visibleAlerts = Array.from(document.querySelectorAll(alertSel))
                    .filter(isVisible)
                    .map(el => (el.innerText || '').trim())
                    .filter(Boolean);

                return {{
                    title: document.title,
                    url: window.location.href,
                    buttons: buttons.slice(0, 50),
                    inputs: inputs.slice(0, 50),
                    headings: headings.slice(0, 20),
                    visible_alerts: visibleAlerts.slice(0, 10),
                }};
            }}""")

            # Take post-action screenshot
            page.screenshot(path={norm_screenshot!r})

            # Save session state if available
            storage_path = os.path.splitext(os.path.abspath(__file__))[0] + ".storage_state.json"
            try:
                context.storage_state(path=storage_path)
            except Exception:
                pass

            # Write post-discovery JSON
            post_payload = {{
                "test_id": {test_id!r},
                "result_url": current_url,
                "dom": dom_data,
                "screenshot": {norm_screenshot!r},
                "network_responses": network_responses[-20:],
                "console_errors": console_errors[-20:],
            }}
            with open({norm_post_json!r}, "w", encoding="utf-8") as f:
                json.dump(post_payload, f, indent=2)

            print(f"[POST_DISCOVERY_FILE] {{{norm_post_json!r}}}")
            print("[ACTION_DONE] {test_id}")

        except Exception as exc:
            try:
                print(f"[FAILURE_URL] {{page.url}}")
                print(f"[ACTION_ERROR] {{str(exc)}}")
                page.screenshot(path={norm_screenshot!r})
            except Exception:
                pass
            raise exc
        finally:
            context.close()
            browser.close()

if __name__ == "__main__":
    run_action()
'''
    return script_template


def action_builder_node(state: ForgeState) -> Dict[str, Any]:
    """
    ACTION_BUILDER node:
    Synthesizes an ActionSpec (executable interaction sequence without speculative assertions)
    and renders an ephemeral action script with post-action observation hooks.
    """
    current_test = state.get("current_test")
    if not current_test:
        test_plan = state.get("test_plan", [])
        curr_idx = state.get("current_test_idx", 0)
        if curr_idx < len(test_plan):
            current_test = test_plan[curr_idx]
            state["current_test"] = current_test
        else:
            raise ValueError("No current_test available in state for action_builder.")

    test_id = current_test.get("id") or current_test.get("test_id") or "test_journey"
    target_url = current_test.get("page_url") or state.get("target_url") or "https://example.com"
    config = state.get("config", {})

    # Resolve target viewport
    vp_config = current_test.get("viewport") or config.get("viewport") or "desktop"
    if isinstance(vp_config, dict):
        vp_width = vp_config.get("width", 1280)
        vp_height = vp_config.get("height", 800)
    elif isinstance(vp_config, str) and vp_config.lower() in FIXED_VIEWPORTS:
        vp_width = FIXED_VIEWPORTS[vp_config.lower()]["width"]
        vp_height = FIXED_VIEWPORTS[vp_config.lower()]["height"]
    else:
        vp_width, vp_height = 1280, 800

    viewport = {"width": vp_width, "height": vp_height}

    # Retrieve available registered accounts for this website if available
    available_accounts: List[Dict[str, Any]] = []
    try:
        website_id = state.get("website_id")
        if website_id:
            available_accounts = ForgeRepository.list_accounts_for_website(website_id)
    except Exception as acc_err:
        logger.warning(f"[ACTION_BUILDER] Could not query accounts: {acc_err}")

    # Prepare discovery context
    discovery_data = state.get("discovery_data") or {}
    elements = discovery_data.get("elements") or {}

    context_payload = {
        "test_id": test_id,
        "test_title": current_test.get("title") or current_test.get("name"),
        "intent": current_test.get("intent") or current_test.get("title"),
        "type": current_test.get("type", "FLOW"),
        "steps": current_test.get("steps", []),
        "target_url": target_url,
        "viewport": viewport,
        "available_accounts": [
            {"username": a.get("username"), "role": a.get("role")}
            for a in available_accounts
        ],
        "discovered_elements": {
            "buttons": elements.get("buttons", [])[:30],
            "inputs": elements.get("inputs", [])[:30],
            "links": elements.get("links", [])[:30],
        },
    }

    logger.info(f"[ACTION_BUILDER] Planning action sequence for '{test_id}' ({current_test.get('type')})")

    llm = get_chat_model()
    messages = [
        SystemMessage(content=ACTION_BUILDER_SYSTEM_PROMPT),
        HumanMessage(content=f"Synthesize ActionSpec for this user journey:\n{json.dumps(context_payload, indent=2, default=str)}")
    ]

    action_spec: ActionSpec
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

        action_dict = json.loads(content)
        action_spec = {
            "test_id": test_id,
            "target_url": action_dict.get("target_url") or target_url,
            "viewport": viewport,
            "steps": action_dict.get("steps", []),
            "storage_state_path": config.get("storage_state_path"),
            "timeout_ms": config.get("timeout_ms", 30000),
        }
    except Exception as e:
        logger.warning(f"[ACTION_BUILDER] LLM synthesis failed ({e}). Generating fallback action spec.")
        fallback_steps: List[ActionStep] = [
            {
                "step_id": 1,
                "action_type": "goto",
                "selector": None,
                "value": target_url,
                "description": f"Navigate to {target_url}",
            }
        ]
        action_spec = {
            "test_id": test_id,
            "target_url": target_url,
            "viewport": viewport,
            "steps": fallback_steps,
            "storage_state_path": config.get("storage_state_path"),
            "timeout_ms": 30000,
        }

    # Determine storage directory and paths
    site_storage = get_website_storage_dir(target_url)
    tests_dir = site_storage / "tests"
    tests_dir.mkdir(parents=True, exist_ok=True)

    action_script_path = tests_dir / f"_temp_action_{test_id}.py"
    post_discovery_json_path = tests_dir / f"_temp_action_{test_id}_post_discovery.json"
    screenshot_path = tests_dir / f"_temp_action_{test_id}_post.png"

    # Render Python script from ActionSpec
    raw_script = render_action_script(
        action_spec=action_spec,
        output_path=action_script_path,
        post_discovery_json_path=post_discovery_json_path,
        screenshot_path=screenshot_path,
    )
    script_code = apply_lint(raw_script, context=f"action_{test_id}")

    with open(action_script_path, "w", encoding="utf-8") as f:
        f.write(script_code)

    logger.info(f"[ACTION_BUILDER] Rendered ephemeral action script ({len(action_spec['steps'])} steps) at: {action_script_path}")

    return {
        "current_test": current_test,
        "action_spec": action_spec,
        "action_code": script_code,
        "action_file_path": str(action_script_path),
        "action_heal_attempt": state.get("action_heal_attempt", 0),
        "max_action_heals": state.get("max_action_heals", 3),
        "max_expectation_heals": state.get("max_expectation_heals", 2),
    }
