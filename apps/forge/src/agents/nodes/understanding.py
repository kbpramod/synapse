import json
import logging
from pathlib import Path
from typing import Any, Dict, List, Optional
from agents.llm import get_chat_model
from agents.state import ForgeState
from langchain_core.messages import SystemMessage, HumanMessage
from storage.local import get_page_folder, save_page_discovery, sanitize_domain
from db.repository import ForgeRepository

logger = logging.getLogger("forge.agent.understanding")

SYSTEM_PROMPT = """You are an expert QA and Web Automation Engineer.
Your task is to analyze discovered DOM metadata for a web page and produce a concise, structured understanding of the page, focusing on user capabilities and state transitions.

Analyze:
1. What type of page is this? (e.g., 'landing_page', 'login', 'signup', 'e-commerce', 'dashboard', 'settings', 'form')
2. What is the core purpose of this page?
3. What user capabilities does this page provide? (e.g. 'contact company', 'authenticate user', 'search content', 'review submission')
4. What state transitions can occur through user interaction? (e.g. 'anonymous -> contact modal visible', 'unauthenticated -> dashboard', 'form unsubmitted -> submission confirmed')
5. What are the primary interactive actions that trigger those transitions?
6. What are the major input fields, forms, and buttons, and what data do they expect?

Return your response strictly in JSON format matching this schema:
{
  "page_type": "string",
  "purpose": "string",
  "capabilities": ["contact company", "explore services"],
  "state_transitions": ["anonymous -> contact experience accessible"],
  "target_roles": ["guest", "user"],
  "primary_actions": ["click contact us", "search product"],
  "key_interactive_elements": [
    {"type": "input|button|form|link", "label": "string", "selector": "string", "expected_input": "string"}
  ],
  "state_preconditions": "string"
}
Output ONLY valid JSON.
"""


def detect_page_model_changes(
    url: str,
    current_disc: Dict[str, Any]
) -> Dict[str, Any]:
    """
    Compares the newly discovered page against the existing saved Page Model.
    Treats differences as observations to refresh the Page Model, NEVER as APP_BUG.
    """
    page_folder = get_page_folder(url)
    existing_file = page_folder / "index.json"

    if not existing_file.exists():
        return {
            "is_first_discovery": True,
            "has_changes": True,
            "observed_changes": ["Initial page model baseline established."],
            "added_count": len(current_disc.get("elements", {}).get("buttons", [])) + len(current_disc.get("elements", {}).get("links", [])),
            "removed_count": 0,
        }

    try:
        prev_data = json.loads(existing_file.read_text(encoding="utf-8"))
        prev_elements = prev_data.get("elements", {})
        curr_elements = current_disc.get("elements", {})

        observed_changes: List[str] = []

        # Compare title
        prev_title = prev_data.get("page", {}).get("title")
        curr_title = current_disc.get("page", {}).get("title")
        if prev_title != curr_title:
            observed_changes.append(f"Page title changed: '{prev_title}' -> '{curr_title}'")

        # Compare buttons
        prev_btn_sels = {b.get("selector") for b in prev_elements.get("buttons", []) if b.get("selector")}
        curr_btn_sels = {b.get("selector") for b in curr_elements.get("buttons", []) if b.get("selector")}
        added_btns = curr_btn_sels - prev_btn_sels
        removed_btns = prev_btn_sels - curr_btn_sels

        if added_btns:
            observed_changes.append(f"{len(added_btns)} new button(s) observed.")
        if removed_btns:
            observed_changes.append(f"{len(removed_btns)} button(s) no longer present in DOM.")

        # Compare links
        prev_lnk_sels = {l.get("selector") for l in prev_elements.get("links", []) if l.get("selector")}
        curr_lnk_sels = {l.get("selector") for l in curr_elements.get("links", []) if l.get("selector")}
        added_lnks = curr_lnk_sels - prev_lnk_sels
        removed_lnks = prev_lnk_sels - curr_lnk_sels

        if added_lnks:
            observed_changes.append(f"{len(added_lnks)} new link(s) observed.")
        if removed_lnks:
            observed_changes.append(f"{len(removed_lnks)} link(s) no longer present in DOM.")

        # Viewport summary changes
        prev_vp = prev_elements.get("viewports_summary", {})
        curr_vp = curr_elements.get("viewports_summary", {})
        if prev_vp.get("mobile_only_count") != curr_vp.get("mobile_only_count"):
            observed_changes.append(
                f"Mobile responsive elements updated: {prev_vp.get('mobile_only_count', 0)} -> {curr_vp.get('mobile_only_count', 0)}"
            )

        has_changes = bool(observed_changes)
        if not observed_changes:
            observed_changes.append("Page model matches previous observation.")

        return {
            "is_first_discovery": False,
            "has_changes": has_changes,
            "observed_changes": observed_changes,
            "added_count": len(added_btns) + len(added_lnks),
            "removed_count": len(removed_btns) + len(removed_lnks),
        }
    except Exception as e:
        logger.warning(f"[PAGE UNDERSTANDING] Failed to read previous page model ({e}). Treating as fresh baseline.")
        return {
            "is_first_discovery": True,
            "has_changes": True,
            "observed_changes": [f"Resetting baseline due to error reading cache: {e}"],
            "added_count": 0,
            "removed_count": 0,
        }


def understanding_node(state: ForgeState) -> Dict[str, Any]:
    """
    APPLICATION MODEL & CHANGE DETECTION node:
    1. Compares newly discovered DOM/viewports against the previous Page Model.
    2. Records differences as observations to refresh the Page Model (never as APP_BUG).
    3. Synthesizes high-level user capabilities and state transitions via LLM.
    4. Persists the updated Page Model to local filesystem and database.
    """
    disc = state.get("discovery_data") or {}
    page_info = disc.get("page", {})
    elements = disc.get("elements", {})
    text_info = disc.get("text", {})
    url = page_info.get("url") or state.get("target_url", "")

    logger.info(f"[PAGE UNDERSTANDING] Analyzing semantics and capabilities for: {url}")

    # 1. Change Detection against existing Page Model
    change_detection = detect_page_model_changes(url, disc)
    logger.info(
        f"[CHANGE DETECTION] Observations for {url}: {change_detection['observed_changes']} "
        f"(Discovery observation to keep Page Model fresh; never treated as APP_BUG)"
    )

    # 2. Build concise context for LLM capability synthesis
    page_summary_input = {
        "url": url,
        "title": page_info.get("title"),
        "headings": text_info.get("headings", [])[:10],
        "buttons": [
            {
                "text": b.get("text"),
                "selector": b.get("selector"),
                "id": b.get("id"),
                "visible_viewports": b.get("visible_viewports", ["desktop"])
            }
            for b in elements.get("buttons", [])[:20]
        ],
        "inputs": [
            {
                "type": inp.get("type"),
                "name": inp.get("name"),
                "placeholder": inp.get("placeholder"),
                "label": inp.get("label"),
                "selector": inp.get("selector"),
                "required": inp.get("required")
            }
            for inp in elements.get("inputs", [])[:15]
        ],
        "forms": [
            {"action": f.get("action"), "method": f.get("method"), "selector": f.get("selector")}
            for f in elements.get("forms", [])[:5]
        ],
        "links_sample": [
            {"text": l.get("text"), "href": l.get("href"), "visible_viewports": l.get("visible_viewports", ["desktop"])}
            for l in elements.get("links", [])[:15]
        ],
        "viewports_summary": elements.get("viewports_summary", {}),
        "body_preview": text_info.get("body_text_preview", "")[:500]
    }

    try:
        llm = get_chat_model()
        messages = [
            SystemMessage(content=SYSTEM_PROMPT),
            HumanMessage(content=f"Discovered Page Context:\n{json.dumps(page_summary_input, indent=2, default=str)}")
        ]
        response = llm.invoke(messages)
        content = response.content.strip()

        # Clean JSON if wrapped in markdown code fence
        if content.startswith("```"):
            lines = content.splitlines()
            if lines[0].startswith("```"):
                lines = lines[1:]
            if lines and lines[-1].startswith("```"):
                lines = lines[:-1]
            content = "\n".join(lines).strip()

        understanding = json.loads(content)
    except Exception as e:
        logger.warning(f"[PAGE UNDERSTANDING] LLM call failed ({e}). Falling back to heuristic capability modeling.")
        understanding = {
            "page_type": "web_page",
            "purpose": page_info.get("title", "Unknown Web Page"),
            "capabilities": ["explore content", "navigate page"],
            "state_transitions": ["anonymous -> page loaded"],
            "target_roles": ["guest"],
            "primary_actions": ["browse", "navigate"],
            "key_interactive_elements": [],
            "state_preconditions": "none"
        }

    # Ensure required fields exist
    if "capabilities" not in understanding:
        understanding["capabilities"] = ["explore content"]
    if "state_transitions" not in understanding:
        understanding["state_transitions"] = ["initial -> loaded"]

    # 3. Save / update Page Model to storage and PostgreSQL
    try:
        save_page_discovery(url, disc)
    except Exception as save_err:
        logger.warning(f"[PAGE UNDERSTANDING] Could not persist page model to disk: {save_err}")

    try:
        domain = sanitize_domain(url)
        ForgeRepository.record_page_discovery(domain, page_info, understanding)
    except Exception as db_err:
        logger.debug(f"[PAGE UNDERSTANDING] Database recording notice: {db_err}")

    logger.info(
        f"[PAGE UNDERSTANDING] Identified {len(understanding.get('capabilities', []))} capabilities, "
        f"{len(understanding.get('state_transitions', []))} state transitions for '{understanding.get('page_type')}'."
    )

    return {
        "page_understanding": understanding,
        "page_model": disc,
        "change_detection": change_detection,
    }
