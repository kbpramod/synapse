import ast
import json
import logging
import re
from pathlib import Path
from typing import Any, Dict, List, Optional
from langchain_core.messages import SystemMessage, HumanMessage
from agents.llm import get_chat_model
from agents.state import ForgeState
from agents.script_lint import apply_lint
from db.repository import ForgeRepository
from storage.local import sanitize_domain, mirror_to_cloud, save_script_revision, _relative_key
from storage.supabase_storage import get_storage_path

logger = logging.getLogger("forge.agent.editor")

EDITOR_SYSTEM_PROMPT = """You are an elite Test Automation Code Editor Agent.
Your task is to SURGICALLY EDIT and REPAIR an existing Playwright test script based on failure diagnostics and a targeted repair plan.

You operate like a professional coding assistant:
1. Carefully inspect the existing test script, the execution error/traceback, and the repair plan.
2. EDIT the code to fix the root cause identified in the diagnosis and fix plan.
3. PRESERVE all existing functionality that already works (navigation, setup, assertions that were not flagged).
4. DO NOT rewrite the script from scratch. Keep the existing function name, imports, and structure.
   EXCEPTION — when the healing plan's `failure_class` is "wrong_expectation", the flagged
   assertion is itself the bug: DELETE or REPLACE it as instructed. Do not try to satisfy it
   with longer waits, `wait_for_selector`, or a looser regex — the element/route being asserted
   does not exist in this application, so waiting for it can never succeed. Rule 3 does not
   protect an assertion the plan identified as wrong.
5. If locator updates are needed, use resilient Playwright locators:
   - In Python: page.get_by_role(...), page.get_by_text(...), page.get_by_label(...), page.get_by_placeholder(...)
   - In TypeScript: page.getByRole(...), page.getByText(...), page.getByLabel(...), page.getByPlaceholder(...)
6. Ensure all required imports (e.g. `re`, `expect`, `sync_playwright`) remain intact.
7. Avoid strict mode violations: if multiple elements share text/roles across responsive viewports, use specific ID selectors (e.g. `page.locator('#id')`) or `.first` (e.g. `page.get_by_role('link', name='...').first`).
8. NEVER INTRODUCE A GUESSED DESTINATION URL. If the failure is "expected URL to be
   /dashboard" (or any route the app never actually navigates to), the correct repair is to
   REMOVE that assertion, not to tweak the pattern. Replace it with a signal that does not
   depend on knowing the destination: the login form/password field is gone, a logout or
   account control is visible, the URL simply differs from the starting URL, or the submit
   response returned a non-error status. Only keep a concrete path if it appears in the
   discovered elements/links.
9. REAL CREDENTIALS: `available_accounts` in the context lists the real registered test
   accounts for this site (username, password, role). If the script signs in, it MUST use
   those exact values literally — never replace them with placeholders like
   "user@example.com" or "your_password", and never remove working credentials while
   repairing something else. Only if that list is empty may you read credentials from
   environment variables.
10. AUTHENTICATION REDIRECT REPAIR:
    If the healing plan diagnoses an authentication redirect (the test navigated to a protected page without an active session):
    - If `storage_state_available` is provided: Update the context creation to reuse the session:
      `context = browser.new_context(viewport={"width": ...}, storage_state="<path>")`
    - If `storage_state_available` is not available: Insert a sign-in interaction sequence at the start of the test using `available_accounts` (e.g. goto login URL, fill username & password, submit, wait for load) before navigating to the target page.
11. CRITICAL ARCHITECTURAL RULE: DOM LOCATORS ARE IMMUTABLE TECHNICAL EVIDENCE:
    - Locators discovered from the DOM are exact technical evidence. Never spell-correct, normalize, or semantically rewrite them during agent-to-agent transfer.
    - If an element selector or attribute from discovered elements contains an apparent typo (e.g. `#susbscribe_email`), preserve it EXACTLY as discovered. Do NOT "correct" it to `#subscribe_email`.
    - If a button has an exact ID (e.g. `#subscribe`), use `page.locator("#subscribe")`. Do NOT use `get_by_role("button", name="...")` if the button has no explicit text.
12. Return ONLY the complete, edited script code without markdown fences, or wrapped in a single ```python or ```typescript code fence.
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


def apply_search_replace_blocks(original_code: str, response_text: str) -> Optional[str]:
    """
    If the response contains SEARCH/REPLACE blocks (similar to Aider / coding agents),
    applies them sequentially to the original code.
    Format:
    <<<<<<< SEARCH
    old lines
    =======
    new lines
    >>>>>>>
    """
    if "<<<<<<< SEARCH" not in response_text or ">>>>>>>" not in response_text:
        return None

    # Normalize line endings
    norm_original = original_code.replace("\r\n", "\n")
    norm_response = response_text.replace("\r\n", "\n")

    modified_code = norm_original
    parts = norm_response.split("<<<<<<< SEARCH")
    for part in parts[1:]:
        if "=======" not in part or ">>>>>>>" not in part:
            continue
        search_block = part.split("=======")[0].strip("\n")
        replace_part = part.split("=======")[1]
        replace_block = replace_part.split(">>>>>>>")[0].strip("\n")

        if search_block in modified_code:
            modified_code = modified_code.replace(search_block, replace_block, 1)
        else:
            # Try line-by-line whitespace-tolerant match
            search_lines = [l.rstrip() for l in search_block.split("\n")]
            code_lines = [l.rstrip() for l in modified_code.split("\n")]
            matched = False
            for idx in range(len(code_lines) - len(search_lines) + 1):
                if code_lines[idx:idx + len(search_lines)] == search_lines:
                    orig_lines = modified_code.split("\n")
                    new_code_lines = orig_lines[:idx] + [replace_block] + orig_lines[idx + len(search_lines):]
                    modified_code = "\n".join(new_code_lines)
                    matched = True
                    break
            if not matched:
                return None

    return modified_code


def editor_node(state: ForgeState) -> Dict[str, Any]:
    """
    EDITOR node: Surgically repairs an existing test script file based on failure
    diagnostics and healing plan, behaving like a code-editing assistant.
    """
    test_file_path_str = state.get("test_file_path")
    if not test_file_path_str:
        raise ValueError("Cannot run editor_node without test_file_path in state.")

    test_path = Path(test_file_path_str)

    # Read existing file content from disk or state
    existing_code = state.get("test_code") or ""
    if test_path.exists():
        try:
            existing_code = test_path.read_text(encoding="utf-8")
        except Exception as e:
            logger.warning(f"[EDITOR] Could not read existing test from disk: {e}")

    if not existing_code:
        raise ValueError(f"Cannot edit test: file {test_path} is empty or does not exist.")

    current_test = state.get("current_test") or {}
    heal_attempt = state.get("heal_attempt", 0)
    healing_plan = state.get("healing_plan") or {}
    healing_history = state.get("healing_history", [])
    exec_res = state.get("execution_result") or {}
    disc = state.get("discovery_data") or {}
    target_url = state.get("target_url") or current_test.get("page_url") or current_test.get("target_url") or ""

    if not disc or not disc.get("elements"):
        try:
            from storage.local import get_discovery_storage_dir, get_page_folder
            if target_url:
                disc_file = get_discovery_storage_dir(target_url) / "discovery.json"
                if disc_file.exists():
                    with open(disc_file, "r", encoding="utf-8") as f:
                        disc = json.load(f)
                else:
                    page_file = get_page_folder(target_url) / "index.json"
                    if page_file.exists():
                        with open(page_file, "r", encoding="utf-8") as f:
                            disc = json.load(f)
        except Exception:
            pass

    if not disc or not disc.get("elements"):
        try:
            if target_url:
                page_rec = ForgeRepository.get_page_by_url(target_url)
                if page_rec and page_rec.get("metadata_json"):
                    disc = page_rec["metadata_json"]
        except Exception:
            pass

    # If healing_plan is not explicitly set, retrieve from latest healing_history
    if not healing_plan and healing_history:
        healing_plan = healing_history[-1]

    diagnosis = healing_plan.get("diagnosis", "Test step or assertion failed.")
    fix_plan = healing_plan.get("fix_plan", "Repair failing locator or wait condition.")
    preserve = healing_plan.get("preserve", "Keep all existing setup and working assertions.")
    failure_class = healing_plan.get("failure_class", "automation_defect")

    logger.info(
        f"[EDITOR] Surgically editing test script: {test_path.name} "
        f"(heal_attempt={heal_attempt}, failure_class={failure_class})"
    )

    # Relevance-rank discovered elements for locator reference so target form buttons/inputs are never omitted
    edit_kw_raw = f"{test_path.stem} {diagnosis} {fix_plan} {exec_res.get('error_summary', '')} {exec_res.get('stderr', '')}"
    edit_keywords = {w.lower() for w in re.findall(r'[a-zA-Z0-9_\-#]+', edit_kw_raw) if len(w) >= 3}

    def _rank_editor_elements(elements_list: list, max_items: int) -> list:
        scored = []
        for el in elements_list:
            score = 0
            el_text = f"{el.get('selector', '')} {el.get('id', '')} {el.get('name', '')} {el.get('text', '')} {el.get('placeholder', '')} {el.get('forge_id', '')}".lower()
            for kw in edit_keywords:
                if kw in el_text:
                    score += 2
            if el.get("id"):
                score += 1
            scored.append((score, el))
        scored.sort(key=lambda item: item[0], reverse=True)
        return [item[1] for item in scored[:max_items]]

    elements_sample = {
        "buttons": [
            {"text": b.get("text"), "selector": b.get("selector"), "id": b.get("id"), "forge_id": b.get("forge_id")}
            for b in _rank_editor_elements(disc.get("elements", {}).get("buttons", []), 35)
        ],
        "inputs": [
            {"name": i.get("name"), "placeholder": i.get("placeholder"), "selector": i.get("selector"), "id": i.get("id"), "forge_id": i.get("forge_id")}
            for i in _rank_editor_elements(disc.get("elements", {}).get("inputs", []), 25)
        ],
        "links": [
            {
                "text": l.get("text"),
                "href": l.get("href"),
                "selector": l.get("selector"),
                "id": l.get("id"),
                "forge_id": l.get("forge_id"),
                "visible_viewports": l.get("visible_viewports", ["desktop"])
            }
            for l in _rank_editor_elements(disc.get("elements", {}).get("links", []), 20)
        ]
    }

    # Real registered test accounts for THIS website, so a repair never swaps working
    # credentials for invented placeholders (a common cause of a "fixed" test failing
    # again at login), and never pulls in another site's accounts.
    try:
        scoped_website_id = state.get("website_id") or current_test.get("website_id")
        if scoped_website_id:
            available_accounts = ForgeRepository.get_credentials_for_website(int(scoped_website_id))
        else:
            available_accounts = ForgeRepository.get_credentials_for_url(state.get("target_url") or "")
    except Exception as acc_err:
        logger.warning(f"[EDITOR] Could not load accounts for {state.get('target_url')}: {acc_err}")
        available_accounts = []

    # Search for an existing storage state session file
    target_url = state.get("target_url") or ""
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

    editor_payload = {
        "file_path": str(test_path),
        "target_url": state.get("target_url"),
        "available_accounts": available_accounts,
        "storage_state_available": storage_state_available,
        "test_scenario": current_test,
        "existing_code": existing_code,
        "last_execution_error": {
            "error_summary": exec_res.get("error_summary"),
            "failure_url": exec_res.get("failure_url"),
            "visible_errors": exec_res.get("visible_errors", []),
            "stderr": (exec_res.get("stderr") or "")[-2000:],
            "stdout": (exec_res.get("stdout") or "")[-1000:],
        },
        "healing_plan": {
            "failure_class": failure_class,
            "diagnosis": diagnosis,
            "fix_plan": fix_plan,
            "preserve": preserve,
        },
        "discovered_elements": elements_sample,
    }

    edit_status = "not_attempted"
    edit_failure_reason = None

    try:
        llm = get_chat_model()
        messages = [
            SystemMessage(content=EDITOR_SYSTEM_PROMPT),
            HumanMessage(
                content=(
                    f"Please surgically edit and repair the following test script.\n\n"
                    f"File to edit: {test_path.name}\n"
                    f"Context & Instructions:\n{json.dumps(editor_payload, indent=2, default=str)}"
                )
            ),
        ]
        response = llm.invoke(messages)
        response_text = response.content.strip()

        # Check if SEARCH/REPLACE blocks were returned
        patched = apply_search_replace_blocks(existing_code, response_text)
        if patched:
            edited_code = patched
        else:
            cleaned = clean_code(response_text)
            if "def test_" in cleaned or "sync_playwright" in cleaned:
                edited_code = cleaned
            else:
                edit_failure_reason = "No SEARCH/REPLACE block or valid full test script found in LLM response"
                edited_code = existing_code

        if test_path.suffix == ".py" and not edit_failure_reason:
            edited_code = apply_lint(edited_code, f"editor/{test_path.name}")

    except Exception as e:
        logger.error(f"[EDITOR] LLM edit failed: {e}. Keeping original code.", exc_info=True)
        edit_failure_reason = str(e)
        edited_code = existing_code

    # Validate with Python AST parsing before saving to prevent corrupting test scripts
    if test_path.suffix == ".py" and not edit_failure_reason:
        try:
            ast.parse(edited_code)
            logger.info(f"[EDITOR] AST validation passed for {test_path.name}.")
        except SyntaxError as syntax_err:
            logger.error(
                f"[EDITOR] AST validation failed: Edited code contains SyntaxError ({syntax_err}). "
                f"Rejecting invalid modification and preserving existing working code."
            )
            edit_failure_reason = f"SyntaxError: {syntax_err}"
            edited_code = existing_code

    if edit_failure_reason:
        edit_status = "failed"
    elif edited_code.strip() != existing_code.strip():
        edit_status = "applied"
    else:
        edit_status = "no_change"

    edit_applied = (edit_status == "applied")

    logger.info(f"[EDITOR] Edit status for {test_path.name}: {edit_status} (applied={edit_applied})")
    if edit_status in ("no_change", "failed"):
        logger.warning(
            f"[EDITOR] NO ACTIONABLE EDIT APPLIED to {test_path.name} (status={edit_status}, reason={edit_failure_reason}). "
            f"The script is byte-identical or invalid. Circuit breaker will halt futile re-runs."
        )

    # Archive the script's lineage for this run. Healing overwrites in place, so without this
    # the previous version and the reason it changed are gone.
    run_id = state.get("run_id") or f"run_{test_path.stem}"
    history_test_id = str(current_test.get("test_id") or current_test.get("id") or test_path.stem)
    history_url = state.get("target_url") or ""
    try:
        # attempt 0 == the code as it stood before any healing touched it.
        if heal_attempt <= 1:
            save_script_revision(
                url_or_domain=history_url,
                test_id=history_test_id,
                run_id=run_id,
                attempt=0,
                code=existing_code,
                metadata={"stage": "pre_heal_baseline"},
            )

        revision_path = save_script_revision(
            url_or_domain=history_url,
            test_id=history_test_id,
            run_id=run_id,
            attempt=heal_attempt,
            code=edited_code,
            metadata={
                "stage": "post_heal",
                "edit_applied": edit_applied,
                "failure_class": failure_class,
                "diagnosis": diagnosis,
                "fix_plan": fix_plan,
                "preserve": preserve,
                "error_summary": exec_res.get("error_summary"),
            },
        )
        logger.info(f"[EDITOR] Archived script revision (attempt {heal_attempt}): {revision_path.name}")
    except Exception as hist_err:
        logger.warning(f"[EDITOR] Could not archive script revision: {hist_err}")

    # Write edited code back to file
    test_path.parent.mkdir(parents=True, exist_ok=True)
    with open(test_path, "w", encoding="utf-8") as f:
        f.write(edited_code)
    mirror_to_cloud(test_path, edited_code, content_type="text/x-python" if test_path.suffix == ".py" else "text/plain")

    logger.info(f"[EDITOR] Successfully saved edited test script to: {test_path}")

    # Update database record if target_url exists
    target_url = state.get("target_url")
    if target_url:
        try:
            domain = sanitize_domain(target_url)
            rel_key = _relative_key(test_path)
            supabase_script_path = get_storage_path(rel_key)
            ForgeRepository.save_test(
                test_id=str(current_test.get("test_id") or current_test.get("id") or test_path.stem),
                domain=domain,
                page_url=target_url,
                title=current_test.get("title", test_path.stem),
                description=current_test.get("description", ""),
                category=current_test.get("category", "regression"),
                priority=current_test.get("priority", "medium"),
                steps=current_test.get("steps", []),
                expected_outcome=current_test.get("expected_outcome", ""),
                script_path=supabase_script_path,
                test_code=edited_code,
                language="python" if test_path.suffix == ".py" else "typescript",
                page_id=state.get("page_id"),
            )
        except Exception as db_err:
            logger.warning(f"[EDITOR] Could not update test in database: {db_err}")

    return {
        "test_code": edited_code,
        "test_file_path": str(test_path),
        "edit_status": edit_status,
        "edit_applied": edit_applied,
    }
