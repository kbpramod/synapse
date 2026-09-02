import json
import logging
from typing import Any, Dict
from agents.llm import get_chat_model
from agents.state import ForgeState
from langchain_core.messages import SystemMessage, HumanMessage

logger = logging.getLogger("forge.agent.understanding")

SYSTEM_PROMPT = """You are an expert QA and Web Automation Engineer.
Your task is to analyze discovered DOM metadata for a web page and produce a concise, structured understanding of the page.

Analyze:
1. What type of page is this? (e.g., 'landing_page', 'login', 'signup', 'e-commerce', 'dashboard', 'settings', 'form')
2. What is the core purpose of this page?
3. What are the key user personas or roles expected?
4. What are the primary user journeys and interactive actions available?
5. What are the major input fields, forms, and buttons, and what data do they expect?

Return your response strictly in JSON format matching this schema:
{
  "page_type": "string",
  "purpose": "string",
  "target_roles": ["guest", "user"],
  "primary_actions": ["click signup", "search product"],
  "key_interactive_elements": [
    {"type": "input|button|form|link", "label": "string", "selector": "string", "expected_input": "string"}
  ],
  "state_preconditions": "string"
}
Output ONLY valid JSON.
"""


def understanding_node(state: ForgeState) -> Dict[str, Any]:
    """
    PAGE UNDERSTANDING node: Uses LangChain + LLM to reason about discovered elements
    and understand what kind of page it is and how users interact with it.
    """
    disc = state.get("discovery_data") or {}
    page_info = disc.get("page", {})
    elements = disc.get("elements", {})
    text_info = disc.get("text", {})

    logger.info(f"[PAGE UNDERSTANDING] Analyzing semantics for: {page_info.get('url')}")

    # Build concise payload for LLM
    page_summary_input = {
        "url": page_info.get("url"),
        "title": page_info.get("title"),
        "headings": text_info.get("headings", [])[:10],
        "buttons": [
            {"text": b.get("text"), "selector": b.get("selector"), "id": b.get("id")}
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
            {"text": l.get("text"), "href": l.get("href")}
            for l in elements.get("links", [])[:15]
        ],
        "body_preview": text_info.get("body_text_preview", "")[:500]
    }

    try:
        llm = get_chat_model()
        messages = [
            SystemMessage(content=SYSTEM_PROMPT),
            HumanMessage(content=f"Discovered Page Context:\n{json.dumps(page_summary_input, indent=2)}")
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
        logger.warning(f"[PAGE UNDERSTANDING] LLM call failed or produced invalid JSON ({e}). Falling back to heuristic understanding.")
        understanding = {
            "page_type": "web_page",
            "purpose": page_info.get("title", "Unknown Web Page"),
            "target_roles": ["guest"],
            "primary_actions": ["browse", "navigate"],
            "key_interactive_elements": [],
            "state_preconditions": "none"
        }

    logger.info(f"[PAGE UNDERSTANDING] Determined page type: '{understanding.get('page_type')}' with {len(understanding.get('primary_actions', []))} primary actions.")
    return {"page_understanding": understanding}
