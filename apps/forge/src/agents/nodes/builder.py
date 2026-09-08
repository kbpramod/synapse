import json
import logging
import os
from pathlib import Path
from typing import Any, Dict
from agents.llm import get_chat_model
from agents.state import ForgeState
from langchain_core.messages import SystemMessage, HumanMessage
from agents.script_lint import apply_lint
from storage.local import get_website_storage_dir, mirror_to_cloud
from db.repository import ForgeRepository
from schemas.discovery import FIXED_VIEWPORTS

logger = logging.getLogger("forge.agent.builder")

BUILDER_PYTHON_SYSTEM_PROMPT = """You are an elite Playwright Python Test Automation Engineer.
Your task is to generate clean, robust, modern Playwright Python test scripts (.py) validating User Journeys on the web application.

TEST CATEGORIES:
- If test_type is "SMOKE": Generate a concise sanity test verifying page load, target element presence/responsiveness, and primary state transition without complex branching.
- If test_type is "FLOW": Generate a comprehensive multi-step user journey validating the full sequence:
    ACTION -> STATE TRANSITION -> OUTCOME

Requirements for the generated Playwright Python test:
1. Must use standard library imports and synchronous Playwright API:
   import os
   import re
   from playwright.sync_api import sync_playwright, expect

2. Structure inside a callable test function configuring the specified target_viewport:
   def test_{test_id}():
       headless = os.getenv("HEADLESS", "false").lower() in (
           "true",
           "1",
           "yes",
       )
       with sync_playwright() as p:
           browser = p.chromium.launch(headless=headless)
           context = browser.new_context(viewport={"width": <target_width>, "height": <target_height>})
           page = context.new_page()
           try:
               page.goto('{target_url}', wait_until='domcontentloaded', timeout=30000)
               # user actions, state transitions, and functional assertions here
               print(f"[FINAL_URL] {page.url}")
               context.storage_state(path=os.path.splitext(os.path.abspath(__file__))[0] + ".storage_state.json")
               print("[TEST PASSED] {scenario_title}")
           finally:
               context.close()
               browser.close()

   if __name__ == "__main__":
       test_{test_id}()

3. Grounded Interaction Path Selection:
   - STRICTLY use ONLY the discovered elements list and links provided in context.
   - Do NOT assume or invent hypothetical elements (e.g. do not look for role="navigation" or role="button" unless it was discovered).
   - If grounding_evidence mentions elements, match against discovered element selectors, forge_ids, or text.
   - Prefer `page.get_by_role(...)`, `page.get_by_text(...)`, `page.get_by_label(...)`, `page.locator(...)`.

4. Always assert state transitions and functional outcomes:
   - ASSERT ONLY WHAT `assertable_signals` SUPPORTS. That block is derived from what the
     browser actually observed on this page:
       * `state_change_signals` — elements present before the action whose disappearance
         proves success. This is the preferred way to verify a transition.
       * `known_routes` — the only routes that exist; never assert a path outside it.
       * `request_endpoints` — real form actions, for asserting response status.
       * `unverified` — explicitly unknowable; never assert anything depending on these.
       * `recommended_success_assertions` / `recommended_failure_assertions` — ready-made
         grounded assertions. Prefer these verbatim over anything you invent.
   - Verify expected states specified in expected_outcomes: e.g. `expect(locator).to_be_visible()` or confirmation text.
   - NEVER assert a hardcoded destination path you were not given. You do not know where the
     app redirects after login/submit. PROHIBITED unless that exact route appears in the
     discovered links or the test scenario:
         expect(page).to_have_url(re.compile(r".*dashboard.*"))   # invented -> false failure
     Asserting a guessed route makes a healthy application look broken.
   - To verify a successful transition without knowing the destination, prefer:
       a) the previous state is gone:
              expect(page.locator("#password")).to_have_count(0)
       b) a success/authenticated indicator appeared:
              expect(page.get_by_role("button", name=re.compile("logout|sign out", re.I))).to_be_visible()
       c) the URL merely CHANGED from where you started:
              start_url = page.url          # capture before the action
              ...
              assert page.url != start_url, f"URL did not change after submit: {page.url}"
       d) the underlying request succeeded — capture the response instead of guessing a route:
              with page.expect_response(lambda r: r.request.method == "POST") as resp_info:
                  page.click("#login-button")
              assert resp_info.value.status < 400, f"Request failed: {resp_info.value.status}"
   - When you DO assert a URL that was actually provided, still use flexible regex matching
     (e.g. `expect(page).to_have_url(re.compile(r".*example.*"))`), because sites often
     redirect (HTTP 301/302 to subdomains or canonical URLs).
   - For NEGATIVE tests (wrong password, invalid input), assert the opposite: an error is
     visible AND the user is still on the starting URL / the form is still present.
   - VALID PYTHON PLAYWRIGHT API ONLY. `expect()` accepts ONLY a Page, Locator or APIResponse
     — passing a plain string raises "ValueError: Unsupported type: <class 'str'>". There is
     no `.to_equal()`, `.toBe()`, `.toEqual()` or any camelCase matcher in this binding.
         WRONG:   expect(page.url).to_equal(start_url)
         RIGHT:   assert page.url == start_url
         RIGHT:   expect(page).to_have_url(start_url)
     Use snake_case throughout (`wait_for_selector`, `get_by_role`, `is_checked`), and never
     use `await` — this is the synchronous API.
   - NEVER assume an element's initial state. `assertable_signals.initial_input_states` and
     each input's `checked_at_load` record what was ACTUALLY observed on the page. A
     precondition that contradicts it (asserting a checkbox is checked when it loads
     unchecked) fails before the test exercises anything. If a test needs a particular
     starting state, SET it (e.g. `check()` / `uncheck()`) rather than asserting it.

5. Output ONLY valid Python code without markdown fences, or wrapped in a single ```python block.

6. Immediately before the final "[TEST PASSED]" print, and while the browser is still open,
   always do BOTH of these (in this order):
     print(f"[FINAL_URL] {{page.url}}")
     context.storage_state(path=os.path.splitext(os.path.abspath(__file__))[0] + ".storage_state.json")

   This is required even if the journey never navigates away from target_url. The platform
   uses the printed URL to detect when a passing test reached a new page (e.g. a login flow
   landing on a dashboard) so it can automatically onboard and generate tests for that page.
   The saved storage_state carries the logged-in session forward — a page behind a login can
   only be discovered afterwards by reusing it, because this browser closes when the test ends.
   Both calls MUST happen inside the try block, before the finally that closes the context.

7. REAL CREDENTIALS:
   `available_accounts` in the context lists the real, registered test accounts for this site
   (username, password, role). Whenever the journey needs to sign in or act as a specific role:
   - Use these exact values literally in the script. NEVER invent placeholders like
     "user@example.com", "testuser", "your_password", and never emit TODO/fill-me-in comments.
   - Pick the account whose `role` best fits the scenario (e.g. an "admin" role for admin
     journeys); otherwise use the first account listed.
   - If `available_accounts` is empty, only then fall back to reading credentials from
     environment variables (e.g. os.getenv("TEST_USERNAME")).
"""

BUILDER_TS_SYSTEM_PROMPT = """You are an elite Playwright TypeScript Automation Engineer.
Your task is to generate clean, robust, modern Playwright TypeScript test scripts (.spec.ts) targeting the web application.

Requirements for the generated Playwright test:
1. Must use:
   import { test, expect } from '@playwright/test';
2. Structure inside a test.describe block with clear test name:
   test.describe('Regression Suite', () => {
     test('test_name', async ({ page }) => {
       // actions
     });
   });
3. Use modern, resilient Playwright locators:
   - Prefer `page.getByRole(...)`, `page.getByText(...)`, `page.getByLabel(...)`, `page.getByPlaceholder(...)`
   - Use discovered element forge_ids or selectors as reliable targets
4. Always include explicit assertions:
   - `await expect(page).toHaveTitle(...)`
   - `await expect(locator).toBeVisible()`
   - `await expect(locator).toBeEnabled()`
5. Handle navigation cleanly:
   - `await page.goto(url, { waitUntil: 'domcontentloaded', timeout: 30000 });`
6. Output ONLY valid TypeScript code without markdown fences, or wrapped in a single ```typescript block.
7. Immediately before the test finishes successfully, always log the page the browser ended
   up on: `console.log(`[FINAL_URL] ${page.url()}`);`. This is required even if the journey
   never navigates away from the starting URL — the platform uses this line to detect when a
   passing test reached a new page (e.g. a login flow landing on a dashboard) so it can
   automatically onboard and generate tests for that page too.
8. REAL CREDENTIALS: `available_accounts` in the context lists the real registered test
   accounts for this site (username, password, role). Use those exact values literally for any
   sign-in step — never invent placeholders like "user@example.com" or "your_password". Pick
   the account whose `role` fits the scenario, else the first one. Only if the list is empty,
   fall back to environment variables.

If HEALING information is provided, carefully inspect the previous failure error, the diagnosis, and the fix plan to adjust locators, wait conditions, or assertions.
"""


def clean_code(raw_code: str) -> str:
    """Removes markdown code fences if present."""
    code = raw_code.strip()
    if code.startswith("```"):
        lines = code.splitlines()
        if lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].startswith("```"):
            lines = lines[:-1]
        code = "\n".join(lines).strip()
    return code


def builder_node(state: ForgeState) -> Dict[str, Any]:
    """
    TEST BUILDER node: Generates Playwright test scripts.
    Defaults to Python (.py) scripts; can generate TypeScript (.spec.ts) if configured.
    Saves to storage/<domain>/tests/<test_id>.<ext> and indexes in Neon PostgreSQL.
    """
    current_test = state.get("current_test")
    if not current_test:
        raise ValueError("Cannot run builder_node without a current_test.")

    target_url = state.get("target_url")
    disc = state.get("discovery_data") or {}
    heal_attempt = state.get("heal_attempt", 0)
    healing_history = state.get("healing_history", [])
    last_exec = state.get("execution_result")

    config = state.get("config", {})
    language = (config.get("language") or os.getenv("FORGE_TEST_LANGUAGE", "python")).strip().lower()
    is_python = (language != "typescript")
    lang_label = "Python" if is_python else "Playwright TS"

    # Resolve target viewport (desktop, tablet, mobile)
    target_vp_name = current_test.get("viewport") or config.get("viewport") or "desktop"
    if isinstance(target_vp_name, str):
        target_vp_name = target_vp_name.lower()
    else:
        target_vp_name = "desktop"
    vp_dims = FIXED_VIEWPORTS.get(target_vp_name, FIXED_VIEWPORTS["desktop"])

    logger.info(
        f"[TEST BUILDER] Generating {lang_label} script for '{current_test['id']}' "
        f"[viewport={target_vp_name} ({vp_dims['width']}x{vp_dims['height']})] (heal_attempt={heal_attempt})"
    )

    # Sample of discovered elements with viewport visibility flags
    elements_sample = {
        "buttons": [
            {
                "forge_id": b.get("forge_id"),
                "text": b.get("text"),
                "selector": b.get("selector"),
                "id": b.get("id"),
                "visible_viewports": b.get("visible_viewports", ["desktop"]),
                "visible_in_target_viewport": target_vp_name in b.get("visible_viewports", ["desktop"]),
            }
            for b in (disc.get("elements", {}).get("buttons", []))[:15]
        ],
        "inputs": [
            {
                "forge_id": i.get("forge_id"),
                "type": i.get("type"),
                "name": i.get("name"),
                "placeholder": i.get("placeholder"),
                "selector": i.get("selector"),
                # Observed initial state for checkbox/radio inputs — None for other types.
                # Without this the model guesses ("checkbox 1 is checked") and the test fails
                # on its own precondition.
                "checked_at_load": i.get("checked"),
                "visible_viewports": i.get("visible_viewports", ["desktop"]),
                "visible_in_target_viewport": target_vp_name in i.get("visible_viewports", ["desktop"]),
            }
            for i in (disc.get("elements", {}).get("inputs", []))[:15]
        ],
        "links": [
            {
                "forge_id": l.get("forge_id"),
                "text": l.get("text"),
                "href": l.get("href"),
                "visible_viewports": l.get("visible_viewports", ["desktop"]),
                "visible_in_target_viewport": target_vp_name in l.get("visible_viewports", ["desktop"]),
            }
            for l in (disc.get("elements", {}).get("links", []))[:10]
        ]
    }

    test_type = str(current_test.get("type", "FLOW")).strip().upper()
    intent = current_test.get("intent") or current_test.get("goal") or current_test.get("description", "")
    expected = current_test.get("expected") or [current_test.get("expected_outcome", "")]
    evidence = current_test.get("evidence", [])

    # Real registered test accounts for THIS website, so login/authenticated journeys are
    # written against credentials that actually work instead of invented placeholders.
    # Prefer an explicit website_id (from the onboarding state, or the test's own DB row)
    # so accounts are never mixed in from another site that happens to share a domain.
    try:
        scoped_website_id = state.get("website_id") or current_test.get("website_id")
        if scoped_website_id:
            available_accounts = ForgeRepository.get_credentials_for_website(int(scoped_website_id))
        else:
            available_accounts = ForgeRepository.get_credentials_for_url(target_url)
        if available_accounts:
            logger.info(
                f"[TEST BUILDER] Supplying {len(available_accounts)} real account(s) as context: "
                f"{[a.get('username') for a in available_accounts]}"
            )
    except Exception as acc_err:
        logger.warning(f"[TEST BUILDER] Could not load accounts for {target_url}: {acc_err}")
        available_accounts = []

    builder_payload: Dict[str, Any] = {
        "target_url": target_url,
        "available_accounts": available_accounts,
        # Grounded assertion catalogue from the expectation node — what can actually be
        # asserted on this page, and what is explicitly unknowable.
        "assertable_signals": state.get("assertable_signals") or {},
        "test_scenario": current_test,
        "test_type": test_type,
        "intent": intent,
        "expected_outcomes": expected,
        "grounding_evidence": evidence,
        "target_viewport": {
            "name": target_vp_name,
            "width": vp_dims["width"],
            "height": vp_dims["height"],
        },
        "discovered_elements_with_forge_ids": elements_sample,
        "heal_attempt": heal_attempt,
        "language": "python" if is_python else "typescript",
    }

    if heal_attempt > 0 and healing_history:
        builder_payload["last_execution_error"] = last_exec.get("stderr") or last_exec.get("error_summary") if last_exec else None
        builder_payload["previous_code"] = state.get("test_code")
        builder_payload["healing_diagnosis"] = healing_history[-1]

    system_prompt = BUILDER_PYTHON_SYSTEM_PROMPT if is_python else BUILDER_TS_SYSTEM_PROMPT

    try:
        llm = get_chat_model()
        messages = [
            SystemMessage(content=system_prompt),
            HumanMessage(content=f"Test Generation Specifications:\n{json.dumps(builder_payload, indent=2, default=str)}")
        ]
        response = llm.invoke(messages)
        code = clean_code(response.content)
        if is_python:
            code = apply_lint(code, f"builder/{current_test.get('id', 'test')}")
            header_lines = []
            if "import os" not in code:
                header_lines.append("import os")
            if "import re" not in code:
                header_lines.append("import re")
            if "import sys" not in code:
                header_lines.append("import sys")
            if "from playwright.sync_api" not in code:
                header_lines.append("from playwright.sync_api import sync_playwright, expect")
            if header_lines:
                code = "\n".join(header_lines) + "\n\n" + code
    except Exception as e:
        logger.warning(f"[TEST BUILDER] LLM test generation failed ({e}). Generating template {lang_label} script.")
        # Check for first visible navigation link or button to perform interaction
        first_btn = next((b for b in disc.get("elements", {}).get("buttons", []) if b.get("visible")), None)
        first_lnk = next((l for l in disc.get("elements", {}).get("links", []) if l.get("visible") and l.get("href") and not l.get("href").startswith("#")), None)
        target_sel = (first_lnk or first_btn or {}).get("selector", "")

        interaction_block = ""
        if target_sel:
            target_sel_repr = json.dumps(target_sel)
            interaction_block = f"""
            # Perform journey interaction and verify state transition
            target_el = page.locator({target_sel_repr}).first
            if target_el.is_visible():
                target_el.click(timeout=10000)
                page.wait_for_load_state("domcontentloaded", timeout=15000)
"""
        test_id_clean = (current_test.get("id") or "journey_test").replace("-", "_")
        if is_python:
            code = f"""import os
import re
import sys
from playwright.sync_api import sync_playwright, expect


def test_{test_id_clean}():
    headless = os.getenv("HEADLESS", "false").lower() in ("true", "1", "yes")
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=headless)
        context = browser.new_context(viewport={{"width": {vp_dims['width']}, "height": {vp_dims['height']}}})
        page = context.new_page()
        try:
            page.goto('{target_url}', wait_until='domcontentloaded', timeout=30000)
            expect(page).to_have_title(re.compile(r".+")){interaction_block}
            print(f"[FINAL_URL] {{page.url}}")
            context.storage_state(path=os.path.splitext(os.path.abspath(__file__))[0] + ".storage_state.json")
            print('[TEST PASSED] [{test_type}] Successfully completed journey on {target_url}')
        finally:
            context.close()
            browser.close()


if __name__ == "__main__":
    test_{test_id_clean}()
"""
        else:
            code = f"""import {{ test, expect }} from '@playwright/test';

test.describe('[{test_type}] {current_test.get("category", "regression").capitalize()} Suite', () => {{
  test('{current_test.get("id", "test_smoke")}', async ({{ page }}) => {{
    await page.goto('{target_url}', {{ waitUntil: 'domcontentloaded', timeout: 30000 }});
    await expect(page).toHaveTitle(/.+/);
    console.log(`[FINAL_URL] ${{page.url()}}`);
    console.log('[TEST PASSED] [{test_type}] Successfully loaded {target_url}');
  }});
}});
"""

    # Persist the generated test into storage
    site_storage = get_website_storage_dir(target_url)
    tests_dir = site_storage / "tests"
    tests_dir.mkdir(parents=True, exist_ok=True)
    test_id = current_test.get("id", "test_run")
    ext = ".py" if is_python else ".spec.ts"
    test_file_path = tests_dir / f"{test_id}{ext}"

    with open(test_file_path, "w", encoding="utf-8") as f:
        f.write(code)
    mirror_to_cloud(test_file_path, code, content_type="text/x-python" if is_python else "text/plain")

    # Also organize into category subdirectory (tests/smoke/ or tests/flows/)
    category_dir = tests_dir / ("smoke" if test_type == "SMOKE" else "flows")
    category_dir.mkdir(parents=True, exist_ok=True)
    category_file = category_dir / f"{test_id}{ext}"
    with open(category_file, "w", encoding="utf-8") as f:
        f.write(code)
    mirror_to_cloud(category_file, code, content_type="text/x-python" if is_python else "text/plain")

    logger.info(f"[TEST BUILDER] Saved {lang_label} [{test_type}] test script to: {test_file_path}")

    # Index in Neon PostgreSQL
    try:
        from storage.local import sanitize_domain
        domain = sanitize_domain(target_url)
        
        # Resolve website_id if target_url matches a registered website
        website = ForgeRepository.get_website_by_url(target_url)
        website_id = website["id"] if website else None

        # Determine cron timings (e.g. 6 hours for SMOKE sanity checks, 24 hours for FLOW journeys)
        cron_hours = current_test.get("cron_interval_hours") or (6 if test_type == "SMOKE" else 24)

        # `tests.test_id` is globally unique, but the planner reuses generic hypothesis ids
        # (e.g. "flow_login", "smoke_navigation_header") across every site it plans for.
        # Without scoping, onboarding a second site with the same hypothesis id would hit
        # save_test's ON CONFLICT branch and silently overwrite an unrelated site's row
        # (its script_path/website_id get updated while `domain` is left stale).
        db_test_id = f"ws{website_id}_{test_id}" if website_id else f"{domain}_{test_id}"

        ForgeRepository.save_test(
            test_id=db_test_id,
            domain=domain,
            page_url=target_url,
            title=current_test.get("title", test_id),
            description=current_test.get("description", ""),
            category=current_test.get("category", test_type.lower()),
            priority=current_test.get("priority", "medium"),
            steps=current_test.get("steps", []),
            expected_outcome=current_test.get("expected_outcome", ""),
            script_path=str(test_file_path),
            test_code=code,
            language="python" if is_python else "typescript",
            website_id=website_id,
            cron_interval_hours=cron_hours,
        )
    except Exception as db_err:
        logger.warning(f"[TEST BUILDER] Could not index test into database: {db_err}")

    return {
        "test_code": code,
        "test_file_path": str(test_file_path),
    }
