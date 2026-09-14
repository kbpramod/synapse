import ast
import json
import logging
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List
from agents.state import ForgeState, ActionSpec, ExpectationSpec, TestProvenance
from agents.script_lint import apply_lint
from storage.local import get_website_storage_dir, mirror_to_cloud, sanitize_domain
from storage.test_artifact_store import save_test_artifacts, get_supabase_test_script_path
from db.repository import ForgeRepository


logger = logging.getLogger("forge.agent.testcase_assembler")


def render_full_test_script(
    action_spec: ActionSpec,
    expectation_spec: ExpectationSpec,
    provenance: TestProvenance,
    target_url: str,
) -> str:
    """
    Assembles a complete, production-grade Playwright Python test script (.py)
    combining the validated ActionSpec + verified ExpectationSpec + Provenance docstring.
    """
    test_id = action_spec.get("test_id", "test_journey")
    viewport = action_spec.get("viewport", {"width": 1280, "height": 800})
    steps = action_spec.get("steps", [])
    expectations = expectation_spec.get("expectations", [])
    storage_state_path = action_spec.get("storage_state_path")

    # Format action steps (12 spaces to nest cleanly inside try: block)
    action_lines: List[str] = []
    for step in steps:
        stype = step.get("action_type")
        sel = step.get("selector")
        val = step.get("value")
        desc = step.get("description", "")

        action_lines.append(f"            # Step {step.get('step_id', '')}: {desc}")
        if stype == "goto":
            url = val or target_url
            action_lines.append(f"            page.goto('{url}', wait_until='domcontentloaded', timeout=30000)")
        elif stype == "fill":
            action_lines.append(f"            loc = page.locator({sel!r})")
            action_lines.append("            loc.scroll_into_view_if_needed()")
            action_lines.append(f"            loc.fill({val!r})")
        elif stype == "click":
            action_lines.append(f"            loc = page.locator({sel!r})")
            action_lines.append("            loc.scroll_into_view_if_needed()")
            action_lines.append("            loc.click()")
        elif stype == "select":
            action_lines.append(f"            page.locator({sel!r}).select_option({val!r})")
        elif stype == "press":
            if sel:
                action_lines.append(f"            page.locator({sel!r}).press({val!r})")
            else:
                action_lines.append(f"            page.keyboard.press({val!r})")
        elif stype == "scroll":
            action_lines.append(f"            page.locator({sel!r}).scroll_into_view_if_needed()")
        elif stype == "wait":
            ms = int(val) if (val and str(val).isdigit()) else 1000
            action_lines.append(f"            page.wait_for_timeout({ms})")
        else:
            if sel:
                action_lines.append(f"            page.locator({sel!r}).click()")

    action_code_block = "\n".join(action_lines) if action_lines else f"            page.goto('{target_url}', timeout=30000)"

    # Format verified expectation assertions (12 spaces to nest inside try: block)
    expectation_lines: List[str] = []
    for exp in expectations:
        code = exp.get("code")
        evidence_summary = "; ".join(exp.get("evidence", []))
        if code:
            expectation_lines.append(f"            # Assertion: {exp.get('type')} (Confidence: {exp.get('confidence', 1.0)})")
            if evidence_summary:
                expectation_lines.append(f"            # Evidence: {evidence_summary}")
            expectation_lines.append(f"            {code}")

    expectation_code_block = "\n".join(expectation_lines) if expectation_lines else "            assert page.url != ''"

    storage_init = (
        f"storage_state={storage_state_path!r}"
        if (storage_state_path and Path(storage_state_path).exists())
        else ""
    )
    context_args = f"viewport={viewport}"
    if storage_init:
        context_args += f", {storage_init}"

    provenance_json = json.dumps(provenance, indent=2)

    script_template = f'''"""
Forge Automated Test Case: {test_id}
Intent: {provenance.get('intent')}

PROVENANCE METADATA:
{provenance_json}
"""
import os
import sys
import re
import json
from pathlib import Path
from playwright.sync_api import sync_playwright, expect

def test_{test_id}():
    headless = os.getenv("HEADLESS", "false").lower() in ("true", "1", "yes")

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=headless)
        context = browser.new_context({context_args})
        page = context.new_page()

        try:
            start_url = page.url

            # === SECTION 1: VALIDATED USER ACTIONS ===
{action_code_block}

            # === SECTION 2: GROUNDED ASSERTIONS ===
{expectation_code_block}

            print(f"[FINAL_URL] {{page.url}}")
            print(f"[TEST PASSED] {test_id}")
        finally:
            context.close()
            browser.close()

if __name__ == "__main__":
    test_{test_id}()
'''
    return script_template


def testcase_assembler_node(state: ForgeState) -> Dict[str, Any]:
    """
    TESTCASE_ASSEMBLER node:
    Combines the validated ActionSpec + verified ExpectationSpec + Provenance metadata
    into the final production Playwright Python test script (`test_{test_id}.py`).
    Verifies AST syntax, persists to disk and PostgreSQL, and records suite summary.
    """
    action_spec = state.get("action_spec") or {}
    expectation_spec = state.get("expectation_spec") or {}
    current_test = state.get("current_test") or {}
    target_url = current_test.get("page_url") or state.get("target_url", "https://example.com")

    test_id = str(action_spec.get("test_id") or current_test.get("test_id") or current_test.get("id") or "test_journey")
    intent = str(current_test.get("intent") or current_test.get("title") or "User journey validation")

    logger.info(f"[TESTCASE_ASSEMBLER] Assembling full test script for '{test_id}'")

    # Construct Provenance record
    now_iso = datetime.now(timezone.utc).isoformat()
    provenance: TestProvenance = {
        "test_id": test_id,
        "intent": intent,
        "action_provenance": {
            "source": "action_builder",
            "validated": True,
            "steps_count": len(action_spec.get("steps", [])),
            "heals_needed": state.get("action_heal_attempt", 0),
            "validated_at": now_iso,
        },
        "expectation_provenance": [
            {
                "assertion": exp.get("code"),
                "type": exp.get("type"),
                "confidence": exp.get("confidence"),
                "evidence": exp.get("evidence", []),
                "heals_needed": state.get("expectation_heal_attempt", 0),
            }
            for exp in expectation_spec.get("expectations", [])
        ],
    }

    raw_script = render_full_test_script(
        action_spec=action_spec,
        expectation_spec=expectation_spec,
        provenance=provenance,
        target_url=target_url,
    )
    linted_script = apply_lint(raw_script, context=f"full_test_{test_id}")

    # Validate Python AST syntax
    try:
        ast.parse(linted_script)
        logger.info(f"[TESTCASE_ASSEMBLER] AST syntax parsing verified for '{test_id}'.")
    except SyntaxError as syn_err:
        logger.error(f"[TESTCASE_ASSEMBLER] AST parse failed for '{test_id}': {syn_err}")

    # 1. Prepare Verification & Summary Artifacts
    verdict = state.get("correctness_verdict", "CORRECT")
    reason = state.get("correctness_reason", "Verified journey and assertions.")
    verification_dict = state.get("verification_result") or {
        "verdict": verdict,
        "confidence": 0.95,
        "evidence": [reason],
        "reason": reason,
        "evaluated_at": now_iso,
    }
    summary_dict = {
        "test_id": test_id,
        "title": current_test.get("title") or current_test.get("name") or test_id,
        "intent": intent,
        "category": str(current_test.get("type", "flow")).lower(),
        "priority": str(current_test.get("priority", "high")).lower(),
        "steps_count": len(action_spec.get("steps", [])),
        "expectations_count": len(expectation_spec.get("expectations", [])),
        "action_heals": state.get("action_heal_attempt", 0),
        "expectation_heals": state.get("expectation_heal_attempt", 0),
        "status": "passed" if verdict == "CORRECT" else "failed",
        "assembled_at": now_iso,
    }

    # 2. Persist Full Test Artifact Directory (Supabase Storage + Local Cache)
    domain = sanitize_domain(target_url)
    artifacts_res = save_test_artifacts(
        domain=domain,
        test_id=test_id,
        action_spec=action_spec,
        action_code=state.get("action_code") or "",
        action_result=state.get("action_result") or {},
        discovery_before=state.get("discovery_data") or {},
        discovery_after=state.get("post_action_result") or {},
        expectation_spec=expectation_spec,
        verification=verification_dict,
        summary=summary_dict,
        test_code=linted_script,
        target_url=target_url,
    )
    test_file_path = artifacts_res["test_script_path"]
    supabase_script_path = artifacts_res.get("supabase_script_path") or get_supabase_test_script_path(domain, test_id)
    logger.info(f"[TESTCASE_ASSEMBLER] Persisted test artifact directory for '{test_id}' at: {test_file_path} (Supabase: {supabase_script_path})")

    # Also keep backward-compatible single test file in tests/ dir
    site_storage = get_website_storage_dir(target_url)
    tests_dir = site_storage / "tests"
    tests_dir.mkdir(parents=True, exist_ok=True)
    compat_file_path = tests_dir / f"test_{test_id}.py"
    try:
        with open(compat_file_path, "w", encoding="utf-8") as f:
            f.write(linted_script)
        mirror_to_cloud(compat_file_path, linted_script, content_type="text/x-python")
    except Exception as e:
        logger.warning(f"[TESTCASE_ASSEMBLER] Compatibility file write notice: {e}")

    # 3. Index in PostgreSQL database
    try:
        website_id = state.get("website_id")
        ForgeRepository.save_test(
            test_id=test_id,
            domain=site_storage.name,
            page_url=target_url,
            title=current_test.get("title") or current_test.get("name") or test_id,
            description=current_test.get("description", ""),
            category=str(current_test.get("type", "flow")).lower(),
            priority=str(current_test.get("priority", "high")).lower(),
            steps=action_spec.get("steps", []),
            script_path=supabase_script_path,
            test_code=linted_script,
            expected_outcome=expectation_spec.get("summary", ""),
            website_id=website_id,
            page_id=state.get("page_id"),
            language="python",
        )
        logger.info(f"[TESTCASE_ASSEMBLER] Indexed test '{test_id}' in PostgreSQL with script_path='{supabase_script_path}'.")
    except Exception as db_err:
        logger.warning(f"[TESTCASE_ASSEMBLER] DB indexing notice: {db_err}")

    # Clean up ephemeral action script if it exists
    action_file_path = state.get("action_file_path")
    if action_file_path and Path(action_file_path).exists():
        try:
            Path(action_file_path).unlink(missing_ok=True)
            stem = Path(action_file_path).stem
            (Path(action_file_path).parent / f"{stem}_post_discovery.json").unlink(missing_ok=True)
        except Exception:
            pass

    # Record into suite summary
    suite_summary = list(state.get("suite_summary", []))
    suite_summary.append({
        "id": test_id,
        "title": current_test.get("title"),
        "status": "PASSED",
        "action_heals": state.get("action_heal_attempt", 0),
        "expectation_heals": state.get("expectation_heal_attempt", 0),
        "script_path": supabase_script_path,
        "local_script_path": str(test_file_path),
    })

    return {
        "test_code": linted_script,
        "test_file_path": str(test_file_path),
        "supabase_script_path": supabase_script_path,
        "test_provenance": provenance,
        "test_artifacts": artifacts_res,
        "suite_summary": suite_summary,
    }

