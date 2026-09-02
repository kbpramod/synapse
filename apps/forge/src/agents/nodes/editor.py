import json
import logging
from pathlib import Path
from typing import Any, Dict, List, Optional
from langchain_core.messages import SystemMessage, HumanMessage
from agents.llm import get_chat_model
from agents.state import ForgeState
from db.repository import ForgeRepository
from storage.local import sanitize_domain

logger = logging.getLogger("forge.agent.editor")

EDITOR_SYSTEM_PROMPT = """You are an elite Test Automation Code Editor Agent.
Your task is to SURGICALLY EDIT and REPAIR an existing Playwright test script based on failure diagnostics and a targeted repair plan.

You operate like a professional coding assistant:
1. Carefully inspect the existing test script, the execution error/traceback, and the repair plan.
2. EDIT the code to fix the root cause identified in the diagnosis and fix plan.
3. PRESERVE all existing functionality that already works (navigation, setup, assertions that were not flagged).
4. DO NOT rewrite the script from scratch. Keep the existing function name, imports, and structure.
5. If locator updates are needed, use resilient Playwright locators:
   - In Python: page.get_by_role(...), page.get_by_text(...), page.get_by_label(...), page.get_by_placeholder(...)
   - In TypeScript: page.getByRole(...), page.getByText(...), page.getByLabel(...), page.getByPlaceholder(...)
6. Ensure all required imports (e.g. `re`, `expect`, `sync_playwright`) remain intact.
7. Return ONLY the complete, edited script code without markdown fences, or wrapped in a single ```python or ```typescript code fence.
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

    modified_code = original_code
    parts = response_text.split("<<<<<<< SEARCH")
    for part in parts[1:]:
        if "=======" not in part or ">>>>>>>" not in part:
            continue
        search_block = part.split("=======")[0].strip("\r\n")
        replace_part = part.split("=======")[1]
        replace_block = replace_part.split(">>>>>>>")[0].strip("\r\n")

        if search_block in modified_code:
            modified_code = modified_code.replace(search_block, replace_block, 1)
        else:
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
            logger.warning(f"[EDITOR] Failed to read {test_path} from disk ({e}), using state test_code.")

    if not existing_code:
        raise ValueError(f"Cannot edit test: file {test_path} is empty or does not exist.")

    current_test = state.get("current_test") or {}
    heal_attempt = state.get("heal_attempt", 0)
    healing_plan = state.get("healing_plan") or {}
    healing_history = state.get("healing_history", [])
    exec_res = state.get("execution_result") or {}
    disc = state.get("discovery_data") or {}

    # If healing_plan is not explicitly set, retrieve from latest healing_history
    if not healing_plan and healing_history:
        healing_plan = healing_history[-1]

    diagnosis = healing_plan.get("diagnosis", "Test step or assertion failed.")
    fix_plan = healing_plan.get("fix_plan", "Repair failing locator or wait condition.")
    preserve = healing_plan.get("preserve", "Keep all existing setup and working assertions.")

    logger.info(f"[EDITOR] Surgically editing test script: {test_path.name} (heal_attempt={heal_attempt})")

    # Discovered elements for locator reference
    elements_sample = {
        "buttons": [
            {"text": b.get("text"), "selector": b.get("selector"), "id": b.get("id"), "forge_id": b.get("forge_id")}
            for b in (disc.get("elements", {}).get("buttons", []))[:15]
        ],
        "inputs": [
            {"name": i.get("name"), "placeholder": i.get("placeholder"), "selector": i.get("selector"), "forge_id": i.get("forge_id")}
            for i in (disc.get("elements", {}).get("inputs", []))[:15]
        ],
        "links": [
            {"text": l.get("text"), "href": l.get("href"), "forge_id": l.get("forge_id")}
            for l in (disc.get("elements", {}).get("links", []))[:10]
        ]
    }

    editor_payload = {
        "file_path": str(test_path),
        "target_url": state.get("target_url"),
        "test_scenario": current_test,
        "existing_code": existing_code,
        "last_execution_error": {
            "error_summary": exec_res.get("error_summary"),
            "stderr": (exec_res.get("stderr") or "")[-2000:],
            "stdout": (exec_res.get("stdout") or "")[-1000:],
        },
        "healing_plan": {
            "diagnosis": diagnosis,
            "fix_plan": fix_plan,
            "preserve": preserve,
        },
        "discovered_elements": elements_sample,
    }

    try:
        llm = get_chat_model()
        messages = [
            SystemMessage(content=EDITOR_SYSTEM_PROMPT),
            HumanMessage(
                content=(
                    f"Please surgically edit and repair the following test script.\n\n"
                    f"File to edit: {test_path.name}\n"
                    f"Context & Instructions:\n{json.dumps(editor_payload, indent=2)}"
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
            edited_code = clean_code(response_text)

    except Exception as e:
        logger.error(f"[EDITOR] LLM edit failed: {e}. Keeping original code.")
        edited_code = existing_code

    # Write edited code back to file
    test_path.parent.mkdir(parents=True, exist_ok=True)
    with open(test_path, "w", encoding="utf-8") as f:
        f.write(edited_code)

    logger.info(f"[EDITOR] Successfully saved edited test script to: {test_path}")

    # Update database record if target_url exists
    target_url = state.get("target_url")
    if target_url:
        try:
            domain = sanitize_domain(target_url)
            ForgeRepository.save_test(
                test_id=current_test.get("id", test_path.stem),
                domain=domain,
                page_url=target_url,
                title=current_test.get("title", test_path.stem),
                description=current_test.get("description", ""),
                category=current_test.get("category", "regression"),
                priority=current_test.get("priority", "medium"),
                steps=current_test.get("steps", []),
                expected_outcome=current_test.get("expected_outcome", ""),
                script_path=str(test_path),
                test_code=edited_code,
                language="python" if test_path.suffix == ".py" else "typescript",
            )
        except Exception as db_err:
            logger.warning(f"[EDITOR] Could not update test in database: {db_err}")

    return {
        "test_code": edited_code,
        "test_file_path": str(test_path),
    }
