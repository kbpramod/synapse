import json
import logging
from pathlib import Path
from typing import Any, Dict, List
from agents.state import ForgeState, PostActionResult, DOMDelta

logger = logging.getLogger("forge.agent.result_discovery")


def calculate_dom_delta(
    pre_discovery: Dict[str, Any],
    post_dom: Dict[str, Any]
) -> DOMDelta:
    """
    Computes a structured before -> after DOM delta:
    - added_elements: new buttons/inputs that appeared after the action
    - removed_elements: buttons/inputs from pre-action that disappeared
    - current_headings: headings present post-action
    - visible_alerts: banners, toasts, and alerts present post-action
    """
    pre_elements = pre_discovery.get("elements") or {}
    pre_buttons = pre_elements.get("buttons") or []
    pre_inputs = pre_elements.get("inputs") or []

    post_buttons = post_dom.get("buttons") or []
    post_inputs = post_dom.get("inputs") or []
    post_headings = post_dom.get("headings") or []
    post_alerts = post_dom.get("visible_alerts") or []

    def get_key(el: Dict[str, Any]) -> str:
        return el.get("id") or el.get("name") or el.get("selector") or el.get("text") or ""

    pre_button_keys = {get_key(b) for b in pre_buttons if get_key(b)}
    pre_input_keys = {get_key(i) for i in pre_inputs if get_key(i)}

    post_button_keys = {get_key(b) for b in post_buttons if get_key(b)}
    post_input_keys = {get_key(i) for i in post_inputs if get_key(i)}

    # Elements removed (disappeared after action, e.g. submitted form controls)
    removed_elements: List[Dict[str, Any]] = []
    for b in pre_buttons:
        k = get_key(b)
        if k and k not in post_button_keys:
            removed_elements.append({"type": "button", "selector": b.get("selector") or f"#{b.get('id')}", "text": b.get("text")})
    for i in pre_inputs:
        k = get_key(i)
        if k and k not in post_input_keys:
            removed_elements.append({"type": "input", "selector": i.get("selector") or f"#{i.get('id')}", "name": i.get("name")})

    # Elements added (appeared after action, e.g. new dashboard cards, logout buttons)
    added_elements: List[Dict[str, Any]] = []
    for b in post_buttons:
        k = get_key(b)
        if k and k not in pre_button_keys:
            added_elements.append({"type": "button", "selector": b.get("selector") or f"#{b.get('id')}", "text": b.get("text")})
    for i in post_inputs:
        k = get_key(i)
        if k and k not in pre_input_keys:
            added_elements.append({"type": "input", "selector": i.get("selector") or f"#{i.get('id')}", "name": i.get("name")})

    return {
        "added_elements": added_elements[:20],
        "removed_elements": removed_elements[:20],
        "changed_text": [],
        "current_headings": post_headings[:20],
        "current_forms": post_dom.get("forms", []),
        "visible_alerts": post_alerts[:10],
    }


def result_discovery_node(state: ForgeState) -> Dict[str, Any]:
    """
    RESULT_DISCOVERY node:
    Reads live post-action snapshot (DOM, URL, network, console, screenshot)
    and computes the structured DOM delta comparing pre-action to post-action.
    """
    action_file_path = state.get("action_file_path")
    target_url = state.get("target_url", "")
    current_test = state.get("current_test") or {}
    start_url = current_test.get("page_url") or target_url

    post_discovery_data: Dict[str, Any] = {}
    if action_file_path:
        stem = Path(action_file_path).stem
        parent = Path(action_file_path).parent
        post_json_file = parent / f"{stem}_post_discovery.json"
        if post_json_file.exists():
            try:
                with open(post_json_file, "r", encoding="utf-8") as f:
                    post_discovery_data = json.load(f)
            except Exception as e:
                logger.warning(f"[RESULT_DISCOVERY] Could not load post-discovery JSON: {e}")

    result_url = post_discovery_data.get("result_url") or start_url
    dom_snapshot = post_discovery_data.get("dom") or {}
    pre_discovery = state.get("discovery_data") or {}

    # Compute DOM delta
    dom_delta = calculate_dom_delta(pre_discovery, dom_snapshot)

    # Navigation summary
    norm_start = start_url.rstrip("/") if start_url else ""
    norm_result = result_url.rstrip("/") if result_url else ""
    url_changed = (norm_start != norm_result)

    exec_res = state.get("action_result") or {}
    post_action_result: PostActionResult = {
        "navigation": {
            "previous_url": start_url,
            "result_url": result_url,
            "url_changed": url_changed,
        },
        "dom_delta": dom_delta,
        "visual": {
            "screenshot_path": post_discovery_data.get("screenshot") or (exec_res.get("screenshot_paths") or [None])[0]
        },
        "network": {
            "responses": post_discovery_data.get("network_responses", []),
        },
        "console": {
            "errors": post_discovery_data.get("console_errors", []),
        },
        "page_metadata": {
            "title": dom_snapshot.get("title", ""),
            "url": result_url,
        },
        "duration_s": exec_res.get("duration_s", 0.0),
    }

    logger.info(
        f"[RESULT_DISCOVERY] URL changed: {url_changed} ('{start_url}' -> '{result_url}'). "
        f"Delta: +{len(dom_delta['added_elements'])} elements, -{len(dom_delta['removed_elements'])} elements, "
        f"{len(dom_delta['visible_alerts'])} alert(s), {len(dom_delta['current_headings'])} heading(s)."
    )

    return {"post_action_result": post_action_result}
