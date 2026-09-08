import json
import logging
import re
from pathlib import Path
from typing import Any, Dict, List, Optional
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
   Every selector, role, text, or route you recommend MUST come directly from `available_elements_in_dom`, `target_url`, or the test scenario intent.
   NEVER recommend non-existent hypothetical selectors (e.g., do not invent role="navigation" or class names not listed).

4. AVOID INVENTED DESTINATION PATHS:
   Never instruct the test to assert a hardcoded redirect route unless that route is explicitly present in the application's discovered links.
   If asserting that an action succeeded without a known route, instruct the Editor to verify:
   a) the previous page/element disappeared, or
   b) an authenticated indicator appeared, or
   c) `page.url != start_url`.

5. AUTHENTICATION REDIRECTS & MISSING PREREQUISITES (CRITICAL):
   When `redirect_detected` is True (e.g. failure_url is the login page while target_url is /inventory or /dashboard)
   OR when `visible_errors_on_page` contains warnings like "You can only access ... when you are logged in", "Epic sadface", "Sign in":
   - The root cause is NOT a broken selector or missing button! It is an authentication requirement.
   - Classify failure_class as "automation_defect".
   - If `storage_state_available` is provided: instruct the Editor to load `browser.new_context(storage_state=...)`.
   - If NO storage_state is available: instruct the Editor to use `available_accounts` to perform login steps FIRST before accessing the target route.

6. REPAIR OUTPUT FORMAT:
   Structure your repair plan clearly:

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
    target_url = state.get("target_url") or current_test.get("page_url") or current_test.get("target_url") or ""
    test_id = current_test.get("id") or current_test.get("test_id") or "unknown_test"

    logger.info("=" * 70)
    logger.info(f"[HEAL] >>> ENTERING HEALER NODE for '{test_id}' (Attempt #{heal_attempt + 1}) <<<")
    logger.info("=" * 70)

    # ---------------------------------------------------------
    # 1. DISCOVERY DATA RECOVERY & INSPECTION
    # ---------------------------------------------------------
    disc = state.get("discovery_data") or {}
    disc_source = "state.discovery_data" if disc else "missing"

    if not disc or not disc.get("elements"):
        # Attempt to load discovery snapshot from disk cache
        try:
            from storage.local import get_discovery_storage_dir, get_page_folder
            if target_url:
                disc_file = get_discovery_storage_dir(target_url) / "discovery.json"
                if disc_file.exists():
                    with open(disc_file, "r", encoding="utf-8") as f:
                        disc = json.load(f)
                        disc_source = f"disk cache ({disc_file})"
                else:
                    page_file = get_page_folder(target_url) / "index.json"
                    if page_file.exists():
                        with open(page_file, "r", encoding="utf-8") as f:
                            disc = json.load(f)
                            disc_source = f"page disk cache ({page_file})"
        except Exception as disk_err:
            logger.debug(f"[HEAL:DISCOVERY] Could not load from disk: {disk_err}")

    # Fallback: check PostgreSQL repository
    if not disc or not disc.get("elements"):
        try:
            from db.repository import ForgeRepository
            if target_url:
                page_rec = ForgeRepository.get_page_by_url(target_url)
                if page_rec and page_rec.get("metadata_json"):
                    disc = page_rec["metadata_json"]
                    disc_source = "database (forge.pages)"
        except Exception as db_err:
            logger.debug(f"[HEAL:DISCOVERY] Could not load from db: {db_err}")

    # Extract discovery elements & metadata
    disc_page = disc.get("page") or {}
    disc_url = disc_page.get("url") or disc.get("url") or ""
    disc_title = disc_page.get("title") or disc.get("title") or ""

    raw_elements = disc.get("elements") or {}
    raw_buttons = raw_elements.get("buttons") or []
    raw_inputs = raw_elements.get("inputs") or []
    raw_links = raw_elements.get("links") or []
    raw_selects = raw_elements.get("selects") or []
    raw_forms = raw_elements.get("forms") or []
    raw_headings = (disc.get("text") or {}).get("headings") or raw_elements.get("headings") or []
    body_text_preview = ((disc.get("text") or {}).get("body_text_preview") or "")[:400]

    logger.info(f"[HEAL:DISCOVERY] Source of discovery data: {disc_source}")
    if disc:
        logger.info(f"[HEAL:DISCOVERY] Snapshot URL   : '{disc_url}' (Target URL: '{target_url}')")
        logger.info(f"[HEAL:DISCOVERY] Snapshot Title : '{disc_title}'")
        logger.info(
            f"[HEAL:DISCOVERY] Raw Elements Discovered: "
            f"Buttons={len(raw_buttons)}, Inputs={len(raw_inputs)}, Links={len(raw_links)}, "
            f"Selects={len(raw_selects)}, Forms={len(raw_forms)}, Headings={len(raw_headings)}"
        )
        if disc_url and target_url and disc_url.rstrip("/") != target_url.rstrip("/"):
            logger.warning(
                f"[HEAL:DISCOVERY:MISMATCH] Discovery snapshot was taken on '{disc_url}', "
                f"which DOES NOT MATCH target URL '{target_url}'! "
                "This indicates discovery redirected (e.g. unauthenticated redirect to login) "
                "or discovery ran on a different page."
            )
    else:
        logger.warning(
            f"[HEAL:DISCOVERY:WARNING] No discovery data found anywhere for '{target_url}'! "
            "Discovery data is empty in state, disk cache, and database. Healer will have no elements to reference."
        )

    # ---------------------------------------------------------
    # 2. LOCATOR / FAILURE DIAGNOSIS AGAINST DISCOVERY ELEMENTS
    # ---------------------------------------------------------
    error_summary = exec_res.get("error_summary") or ""
    stderr = exec_res.get("stderr") or ""
    # Extract any targeted selector or text from error messages
    targeted_selector = None
    selector_match = re.search(r'''(?:locator|wait_for_selector|click|fill|select_option)\s*\(\s*['"]([^'"]+)['"]''', error_summary + " " + stderr)
    if selector_match:
        targeted_selector = selector_match.group(1)
    elif "Timeout" in error_summary and "#" in error_summary:
        hash_match = re.search(r'(#[\w\-]+)', error_summary)
        if hash_match:
            targeted_selector = hash_match.group(1)

    if targeted_selector:
        in_buttons = [b for b in raw_buttons if targeted_selector in (b.get("selector") or "") or targeted_selector in (b.get("id") or "") or targeted_selector in (b.get("text") or "")]
        in_inputs = [i for i in raw_inputs if targeted_selector in (i.get("selector") or "") or targeted_selector in (i.get("id") or "") or targeted_selector in (i.get("name") or "")]
        in_links = [l for l in raw_links if targeted_selector in (l.get("selector") or "") or targeted_selector in (l.get("id") or "") or targeted_selector in (l.get("text") or "")]

        if in_buttons or in_inputs or in_links:
            logger.info(
                f"[HEAL:LOCATOR_MATCH] Target '{targeted_selector}' WAS OBSERVED in discovery: "
                f"Buttons={len(in_buttons)}, Inputs={len(in_inputs)}, Links={len(in_links)}"
            )
        else:
            logger.warning(
                f"[HEAL:LOCATOR_MATCH] Target '{targeted_selector}' was NOT OBSERVED in discovery snapshot of '{disc_url}'! "
                f"Reason: The element was never rendered on that page, or the browser landed on a different route."
            )

    # ---------------------------------------------------------
    # 3. BUILD FILTERED DOM ELEMENTS FOR LLM
    # ---------------------------------------------------------
    # Ensure any element matching targeted_selector is prioritized at the top
    def _prioritize(elements_list: list, match_str: Optional[str]) -> list:
        if not match_str:
            return elements_list
        matched = []
        unmatched = []
        for el in elements_list:
            el_str = f"{el.get('selector', '')} {el.get('id', '')} {el.get('name', '')} {el.get('text', '')}"
            if match_str in el_str:
                matched.append(el)
            else:
                unmatched.append(el)
        return matched + unmatched

    prioritized_buttons = _prioritize(raw_buttons, targeted_selector)
    prioritized_inputs = _prioritize(raw_inputs, targeted_selector)
    prioritized_links = _prioritize(raw_links, targeted_selector)

    # Slicing limits to fit LLM context window
    MAX_BUTTONS, MAX_INPUTS, MAX_LINKS, MAX_SELECTS = 30, 20, 30, 10
    sliced_buttons = prioritized_buttons[:MAX_BUTTONS]
    sliced_inputs = prioritized_inputs[:MAX_INPUTS]
    sliced_links = prioritized_links[:MAX_LINKS]
    sliced_selects = raw_selects[:MAX_SELECTS]

    available_elements = {
        "buttons": [
            {
                "text": b.get("text"),
                "selector": b.get("selector"),
                "id": b.get("id"),
                "forge_id": b.get("forge_id"),
                "role": b.get("role"),
                "aria_label": b.get("aria_label"),
            }
            for b in sliced_buttons
        ],
        "inputs": [
            {
                "name": i.get("name"),
                "placeholder": i.get("placeholder"),
                "selector": i.get("selector"),
                "id": i.get("id"),
                "forge_id": i.get("forge_id"),
                "type": i.get("type"),
                "label": i.get("label"),
            }
            for i in sliced_inputs
        ],
        "links": [
            {
                "text": l.get("text"),
                "selector": l.get("selector"),
                "id": l.get("id"),
                "forge_id": l.get("forge_id"),
                "href": l.get("href"),
            }
            for l in sliced_links
        ],
        "selects": [
            {
                "name": s.get("name"),
                "selector": s.get("selector"),
                "id": s.get("id"),
                "forge_id": s.get("forge_id"),
                "options": [o.get("text") for o in s.get("options", [])][:5],
            }
            for s in sliced_selects
        ],
    }

    logger.info(
        f"[HEAL:ELEMENTS_FEED] Included in Healer prompt: "
        f"{len(available_elements['buttons'])}/{len(raw_buttons)} buttons, "
        f"{len(available_elements['inputs'])}/{len(raw_inputs)} inputs, "
        f"{len(available_elements['links'])}/{len(raw_links)} links, "
        f"{len(available_elements['selects'])}/{len(raw_selects)} selects."
    )
    if len(raw_buttons) > MAX_BUTTONS or len(raw_links) > MAX_LINKS:
        logger.info(
            f"[HEAL:ELEMENTS_FEED:OMITTED] "
            f"Omitted {max(0, len(raw_buttons) - MAX_BUTTONS)} buttons and "
            f"{max(0, len(raw_links) - MAX_LINKS)} links to protect LLM context limits."
        )

    # Detailed sample of elements going to healer
    sample_btns = [b.get("selector") or b.get("text") or b.get("id") for b in available_elements["buttons"][:10]]
    sample_inps = [i.get("selector") or i.get("name") or i.get("placeholder") for i in available_elements["inputs"][:10]]
    logger.info(f"[HEAL:ELEMENTS_FEED] Buttons passed to Healer: {sample_btns}")
    logger.info(f"[HEAL:ELEMENTS_FEED] Inputs passed to Healer: {sample_inps}")

    # ---------------------------------------------------------
    # 4. TELEMETRY & RUNTIME STATE
    # ---------------------------------------------------------
    code_to_heal = state.get("test_code") or ""
    test_file_path = state.get("test_file_path")
    if not code_to_heal and test_file_path and Path(test_file_path).exists():
        try:
            with open(test_file_path, "r", encoding="utf-8") as f:
                code_to_heal = f.read()
        except Exception:
            pass

    failure_url = exec_res.get("failure_url")
    redirect_detected = False
    if failure_url and target_url:
        clean_fail = failure_url.split("?")[0].rstrip("/")
        clean_target = target_url.split("?")[0].rstrip("/")
        if clean_fail != clean_target:
            redirect_detected = True

    visible_errors = exec_res.get("visible_errors", [])
    page_headings = [h.get("text") if isinstance(h, dict) else str(h) for h in raw_headings[:8]]

    # Storage state session files
    storage_state_available = None
    try:
        from storage.local import get_website_storage_dir
        if target_url:
            site_storage = get_website_storage_dir(target_url)
            tests_dir = site_storage / "tests"
            if tests_dir.exists():
                session_files = list(tests_dir.glob("**/*.storage_state.json"))
                if session_files:
                    storage_state_available = str(session_files[0].resolve()).replace("\\", "/")
    except Exception:
        pass

    # Available accounts
    available_accounts = []
    try:
        from db.repository import ForgeRepository
        scoped_website_id = state.get("website_id") or current_test.get("website_id")
        if scoped_website_id:
            accounts = ForgeRepository.get_credentials_for_website(int(scoped_website_id))
        else:
            accounts = ForgeRepository.get_credentials_for_url(target_url)
        available_accounts = [{"username": a.get("username"), "role": a.get("role")} for a in accounts]
    except Exception:
        pass

    logger.info(
        f"[HEAL:TELEMETRY] Target URL: '{target_url}' | Failure URL: '{failure_url}' | Redirect Detected: {redirect_detected}"
    )
    logger.info(f"[HEAL:TELEMETRY] Visible Errors on Page: {visible_errors}")
    logger.info(f"[HEAL:TELEMETRY] Storage State Available: {storage_state_available or 'None'}")
    logger.info(f"[HEAL:TELEMETRY] Registered Accounts Available: {[a['username'] for a in available_accounts] or 'None'}")
    logger.info(f"[HEAL:TELEMETRY] Error Summary: '{exec_res.get('error_summary')}'")

    # ---------------------------------------------------------
    # 5. ASSEMBLE FULL HEALER PAYLOAD
    # ---------------------------------------------------------
    healer_payload = {
        "test_id": test_id,
        "test_intent": current_test.get("intent") or current_test.get("goal") or current_test.get("description"),
        "target_url": target_url,
        "failure_url": failure_url,
        "redirect_detected": redirect_detected,
        "visible_errors_on_page": visible_errors,
        "page_headings": page_headings,
        "body_text_preview": body_text_preview,
        "available_accounts": available_accounts,
        "storage_state_available": storage_state_available,
        "expected_outcomes": current_test.get("expected") or [current_test.get("expected_outcome")],
        "error_summary": exec_res.get("error_summary"),
        "stderr": (exec_res.get("stderr") or "")[-2000:],
        "suggested_analysis": analysis.get("suggested_fix"),
        "available_elements_in_dom": available_elements,
        "previous_heal_attempts": healing_history[-3:],
        "test_code": code_to_heal[-2000:]
    }

    payload_json = json.dumps(healer_payload, indent=2, default=str)
    logger.info(f"[HEAL:LLM_DISPATCH] Dispatching payload ({len(payload_json)} chars) to Healer LLM...")

    # ---------------------------------------------------------
    # 6. INVOKE LLM
    # ---------------------------------------------------------
    try:
        llm = get_chat_model()
        messages = [
            SystemMessage(content=HEALER_SYSTEM_PROMPT),
            HumanMessage(content=f"Failure Context for Healing:\n{payload_json}")
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

        logger.info(f"[HEAL:LLM_RESULT] Success!")
        logger.info(f"[HEAL:LLM_RESULT]   * Failure Class : {failure_class}")
        logger.info(f"[HEAL:LLM_RESULT]   * Diagnosis     : {diagnosis}")
        logger.info(f"[HEAL:LLM_RESULT]   * Fix Plan      : {fix_plan}")
        logger.info(f"[HEAL:LLM_RESULT]   * Preserve      : {preserve}")

    except Exception as e:
        logger.warning(f"[HEAL:LLM_FALLBACK] LLM call failed ({e}). Using fallback heuristic.")
        if redirect_detected or any(re.search(r"\b(log\s*in|sign\s*in|authenticated|unauthorized)\b", err, re.I) for err in visible_errors):
            diagnosis = f"Application redirected to '{failure_url}'. Visible errors: {visible_errors}."
            fix_plan = f"Initialize session with storage_state: '{storage_state_available}'." if storage_state_available else "Prepend login steps."
            preserve, failure_class = "Keep test assertions.", "automation_defect"
        else:
            diagnosis, fix_plan, preserve, failure_class = "Timeout failure.", "Add wait_for_load_state('networkidle').", "Keep assertions.", "automation_defect"

        logger.info(f"[HEAL:FALLBACK] Diagnosis: {diagnosis}")
        logger.info(f"[HEAL:FALLBACK] Fix Plan : {fix_plan}")
        logger.info(f"[HEAL:FALLBACK] Preserve : {preserve}")

    heal_event: HealEvent = {
        "attempt": heal_attempt + 1,
        "test_id": test_id,
        "error_snippet": exec_res.get("error_summary", "")[:200],
        "failure_class": failure_class,
        "diagnosis": diagnosis,
        "fix_plan": fix_plan,
        "preserve": preserve,
    }
    healing_history.append(heal_event)

    logger.info("=" * 70)
    logger.info(f"[HEAL] Completed Healer node for '{test_id}'. Routing to Editor.")
    logger.info("=" * 70)

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
