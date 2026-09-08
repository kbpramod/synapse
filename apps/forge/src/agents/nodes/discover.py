import logging
from typing import Any, Dict
from config import is_headless
from browser.discovery import discover_page_sync
from schemas.discovery import StateInfo
from agents.state import ForgeState

logger = logging.getLogger("forge.agent.discover")


def discover_node(state: ForgeState) -> Dict[str, Any]:
    """
    DISCOVER node: Uses Playwright to inspect the target page and extract:
    - Interactive elements (buttons, inputs, selects, links, forms)
    - Content hierarchy (headings, text preview)
    - Runtime console messages and network errors
    """
    target_url = state.get("target_url")
    if not target_url:
        raise ValueError("Cannot run discover_node without target_url in state.")

    config = state.get("config", {})
    headless = config.get("headless")
    if headless is None:
        headless = is_headless()
    timeout_ms = config.get("timeout_ms", 30000)
    settle_ms = config.get("settle_ms", 1500)

    # Viewport resolution (desktop, tablet, mobile, or dict)
    vp_config = config.get("viewport")
    vp_width = 1280
    vp_height = 800
    if isinstance(vp_config, dict):
        vp_width = vp_config.get("width", 1280)
        vp_height = vp_config.get("height", 800)
    elif isinstance(vp_config, str):
        from schemas.discovery import FIXED_VIEWPORTS
        vp_key = vp_config.lower()
        if vp_key in FIXED_VIEWPORTS:
            vp_width = FIXED_VIEWPORTS[vp_key]["width"]
            vp_height = FIXED_VIEWPORTS[vp_key]["height"]

    logger.info(f"[DISCOVER] Running browser discovery on: {target_url} (base viewport {vp_width}x{vp_height})")

    default_state = StateInfo(
        name="default",
        role=config.get("default_role", "guest"),
        is_authenticated=config.get("is_authenticated", False),
    )

    result = discover_page_sync(
        url=target_url,
        state_info=default_state,
        headless=headless,
        viewport_width=vp_width,
        viewport_height=vp_height,
        timeout_ms=timeout_ms,
        settle_ms=settle_ms,
        save_to_storage=True,
        storage_state=config.get("storage_state_path"),
    )

    data_dict = result.model_dump()
    vp_summary = data_dict.get("elements", {}).get("viewports_summary") or {}
    logger.info(
        f"[DISCOVER] Completed for {target_url}: Found "
        f"{len(data_dict.get('elements', {}).get('buttons', []))} buttons, "
        f"{len(data_dict.get('elements', {}).get('inputs', []))} inputs, "
        f"{len(data_dict.get('elements', {}).get('links', []))} links. "
        f"Viewports: Desktop-only={vp_summary.get('desktop_only_count', 0)}, "
        f"Mobile-only={vp_summary.get('mobile_only_count', 0)}, "
        f"All-viewports={vp_summary.get('all_viewports_count', 0)}."
    )

    return {"discovery_data": data_dict}
