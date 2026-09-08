import argparse
import asyncio
import fnmatch
import json
import logging
from collections import deque
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple
from urllib.parse import parse_qsl, urlencode, urljoin, urlparse, urlunparse

from playwright.async_api import Browser, BrowserContext, Page, async_playwright
from playwright.sync_api import sync_playwright

try:
    from schemas.discovery import (
        BoundingBox,
        ButtonElement,
        ConsoleMessage,
        DialogElement,
        DiscoveredElements,
        DiscoveryResult,
        FailedRequest,
        FIXED_VIEWPORTS,
        FormElement,
        HeadingElement,
        ImageElement,
        InputElement,
        LinkElement,
        PageInfo,
        PageNode,
        PageViewState,
        RuntimeInfo,
        SelectElement,
        SelectOption,
        SiteDiscoveryConfig,
        SiteDiscoveryResult,
        StateInfo,
        TextareaElement,
        TextSummary,
        Viewport,
        ViewportSummary,
    )
    from storage.local import (
        get_page_folder,
        sanitize_domain,
        sanitize_page_slug,
        save_discovery_result,
        save_page_discovery,
        save_site_discovery,
    )
except (ImportError, ValueError):
    from ..schemas.discovery import (
        BoundingBox,
        ButtonElement,
        ConsoleMessage,
        DialogElement,
        DiscoveredElements,
        DiscoveryResult,
        FailedRequest,
        FIXED_VIEWPORTS,
        FormElement,
        HeadingElement,
        ImageElement,
        InputElement,
        LinkElement,
        PageInfo,
        PageNode,
        PageViewState,
        RuntimeInfo,
        SelectElement,
        SelectOption,
        SiteDiscoveryConfig,
        SiteDiscoveryResult,
        StateInfo,
        TextareaElement,
        TextSummary,
        Viewport,
        ViewportSummary,
    )
    from ..storage.local import (
        get_page_folder,
        sanitize_domain,
        sanitize_page_slug,
        save_discovery_result,
        save_page_discovery,
        save_site_discovery,
    )

try:
    from config import is_headless
except (ImportError, ValueError):
    from ..config import is_headless

logger = logging.getLogger("forge.discovery")

TRACKING_QUERY_PARAMS = {
    "utm_source", "utm_medium", "utm_campaign", "utm_term", "utm_content",
    "ref", "fbclid", "gclid", "msclkid", "mc_cid", "mc_eid"
}

DOM_EXTRACTION_SCRIPT = """() => {
    const isVisible = (el) => {
        if (!el) return false;
        const style = window.getComputedStyle(el);
        if (style.display === 'none' || style.visibility === 'hidden' || style.opacity === '0') return false;
        const rect = el.getBoundingClientRect();
        return rect.width > 0 && rect.height > 0;
    };

    const getRect = (el) => {
        const r = el.getBoundingClientRect();
        return {
            x: Math.round(r.x),
            y: Math.round(r.y),
            width: Math.round(r.width),
            height: Math.round(r.height)
        };
    };

    const isInViewport = (el) => {
        if (!isVisible(el)) return false;
        const r = el.getBoundingClientRect();
        return (
            r.bottom > 0 &&
            r.right > 0 &&
            r.top < window.innerHeight &&
            r.left < window.innerWidth
        );
    };

    const makeVpProps = (el) => {
        const vis = isVisible(el);
        return {
            visible: vis,
            in_viewport: vis ? isInViewport(el) : false,
            visible_viewports: vis ? ['desktop'] : [],
            viewport_visibility: { desktop: vis }
        };
    };

    const getBestSelector = (el) => {
        if (el.id) return `#${CSS.escape(el.id)}`;
        if (el.getAttribute('data-testid')) return `[data-testid="${CSS.escape(el.getAttribute('data-testid'))}"]`;
        if (el.getAttribute('name')) return `[name="${CSS.escape(el.getAttribute('name'))}"]`;
        if (el.getAttribute('aria-label')) return `[aria-label="${CSS.escape(el.getAttribute('aria-label'))}"]`;
        
        const tag = el.tagName.toLowerCase();
        if (tag === 'a' && el.getAttribute('href')) {
            const rawHref = el.getAttribute('href');
            if (rawHref.length < 50 && !rawHref.includes('"')) {
                return `a[href="${CSS.escape(rawHref)}"]`;
            }
        }
        
        if (el.className && typeof el.className === 'string') {
            const validClasses = el.className
                .trim()
                .split(/\\s+/)
                .filter(c => c && !c.includes(':') && !c.includes('/') && !c.includes('[') && !c.includes(']'))
                .slice(0, 2);
            if (validClasses.length > 0) {
                return `${tag}.${validClasses.join('.')}`;
            }
        }
        return tag;
    };

    const getLabel = (el) => {
        if (el.getAttribute('aria-label')) return el.getAttribute('aria-label').trim();
        if (el.getAttribute('aria-labelledby')) {
            const refEl = document.getElementById(el.getAttribute('aria-labelledby'));
            if (refEl) return refEl.innerText.trim();
        }
        if (el.id) {
            const label = document.querySelector(`label[for="${CSS.escape(el.id)}"]`);
            if (label) return label.innerText.trim();
        }
        const parentLabel = el.closest('label');
        if (parentLabel) {
            const clone = parentLabel.cloneNode(true);
            const childInputs = clone.querySelectorAll('input, select, textarea');
            childInputs.forEach(ci => ci.remove());
            const text = clone.innerText.trim();
            if (text) return text;
        }
        if (el.placeholder) return el.placeholder.trim();
        if (el.title) return el.title.trim();
        return null;
    };

    const toSlug = (str, fallback) => {
        if (!str) return fallback;
        const cleaned = String(str).toLowerCase().replace(/[^a-z0-9]+/g, '_').replace(/^_+|_+$/g, '').slice(0, 20);
        return cleaned || fallback;
    };

    // 1. Buttons
    const buttons = [];
    document.querySelectorAll('button, input[type="button"], input[type="submit"], input[type="reset"], [role="button"], a.btn, a.button').forEach((el, idx) => {
        const text = el.innerText ? el.innerText.trim() : (el.value || el.getAttribute('aria-label') || el.getAttribute('title') || '');
        const forgeId = el.id ? `btn_${toSlug(el.id, 'id')}` : `btn_${toSlug(text, 'action')}_${idx + 1}`;
        const vp = makeVpProps(el);
        buttons.push({
            forge_id: forgeId,
            text: text,
            role: el.getAttribute('role') || (el.tagName.toLowerCase() === 'button' ? 'button' : el.tagName.toLowerCase()),
            type: el.type || 'button',
            id: el.id || null,
            name: el.name || null,
            visible: vp.visible,
            in_viewport: vp.in_viewport,
            visible_viewports: vp.visible_viewports,
            viewport_visibility: vp.viewport_visibility,
            enabled: !el.disabled && !el.hasAttribute('disabled') && el.getAttribute('aria-disabled') !== 'true',
            selector: getBestSelector(el),
            bounding_box: getRect(el)
        });
    });

    // 2. Links
    const links = [];
    document.querySelectorAll('a[href]').forEach((el, idx) => {
        const rawHref = el.getAttribute('href') || '';
        const text = el.innerText ? el.innerText.trim() : (el.getAttribute('aria-label') || el.getAttribute('title') || '');
        const forgeId = el.id ? `lnk_${toSlug(el.id, 'id')}` : `lnk_${toSlug(text, 'nav')}_${idx + 1}`;
        const vp = makeVpProps(el);
        links.push({
            forge_id: forgeId,
            text: text,
            href: el.href || rawHref,
            raw_href: rawHref,
            id: el.id || null,
            target: el.target || null,
            visible: vp.visible,
            in_viewport: vp.in_viewport,
            visible_viewports: vp.visible_viewports,
            viewport_visibility: vp.viewport_visibility,
            selector: getBestSelector(el),
            bounding_box: getRect(el)
        });
    });

    // 3. Inputs
    const inputs = [];
    document.querySelectorAll('input:not([type="button"]):not([type="submit"]):not([type="reset"]):not([type="hidden"])').forEach((el, idx) => {
        const labelText = getLabel(el);
        const forgeId = el.id ? `inp_${toSlug(el.id, 'id')}` : `inp_${toSlug(el.name || el.placeholder || labelText, 'field')}_${idx + 1}`;
        const vp = makeVpProps(el);
        inputs.push({
            forge_id: forgeId,
            type: el.type || 'text',
            name: el.name || null,
            id: el.id || null,
            placeholder: el.placeholder || null,
            label: labelText,
            value: el.value || '',
            required: el.required || el.hasAttribute('required'),
            disabled: el.disabled || el.hasAttribute('disabled'),
            checked: el.type === 'checkbox' || el.type === 'radio' ? el.checked : null,
            visible: vp.visible,
            in_viewport: vp.in_viewport,
            visible_viewports: vp.visible_viewports,
            viewport_visibility: vp.viewport_visibility,
            selector: getBestSelector(el),
            bounding_box: getRect(el)
        });
    });

    // 4. Textareas
    const textareas = [];
    document.querySelectorAll('textarea').forEach((el, idx) => {
        const labelText = getLabel(el);
        const forgeId = el.id ? `txt_${toSlug(el.id, 'id')}` : `txt_${toSlug(el.name || el.placeholder || labelText, 'area')}_${idx + 1}`;
        const vp = makeVpProps(el);
        textareas.push({
            forge_id: forgeId,
            name: el.name || null,
            id: el.id || null,
            placeholder: el.placeholder || null,
            label: labelText,
            value: el.value || '',
            required: el.required || el.hasAttribute('required'),
            disabled: el.disabled || el.hasAttribute('disabled'),
            visible: vp.visible,
            in_viewport: vp.in_viewport,
            visible_viewports: vp.visible_viewports,
            viewport_visibility: vp.viewport_visibility,
            selector: getBestSelector(el),
            bounding_box: getRect(el)
        });
    });

    // 5. Selects
    const selects = [];
    document.querySelectorAll('select').forEach((el, idx) => {
        const labelText = getLabel(el);
        const forgeId = el.id ? `sel_${toSlug(el.id, 'id')}` : `sel_${toSlug(el.name || labelText, 'select')}_${idx + 1}`;
        const options = [];
        el.querySelectorAll('option').forEach(opt => {
            options.push({
                text: opt.innerText ? opt.innerText.trim() : opt.value,
                value: opt.value,
                selected: opt.selected
            });
        });
        const vp = makeVpProps(el);
        selects.push({
            forge_id: forgeId,
            name: el.name || null,
            id: el.id || null,
            label: labelText,
            options: options,
            disabled: el.disabled || el.hasAttribute('disabled'),
            required: el.required || el.hasAttribute('required'),
            visible: vp.visible,
            in_viewport: vp.in_viewport,
            visible_viewports: vp.visible_viewports,
            viewport_visibility: vp.viewport_visibility,
            selector: getBestSelector(el),
            bounding_box: getRect(el)
        });
    });

    // 6. Forms
    const forms = [];
    document.querySelectorAll('form').forEach(el => {
        const vp = makeVpProps(el);
        forms.push({
            id: el.id || null,
            name: el.name || null,
            action: el.action || null,
            method: (el.method || 'GET').toUpperCase(),
            input_count: el.querySelectorAll('input, textarea, select').length,
            button_count: el.querySelectorAll('button, input[type="submit"]').length,
            visible: vp.visible,
            in_viewport: vp.in_viewport,
            visible_viewports: vp.visible_viewports,
            viewport_visibility: vp.viewport_visibility,
            selector: getBestSelector(el),
            bounding_box: getRect(el)
        });
    });

    // 7. Dialogs & Modals
    const dialogs = [];
    document.querySelectorAll('dialog, [role="dialog"], [role="alertdialog"], .modal, .popup').forEach(el => {
        const vp = makeVpProps(el);
        dialogs.push({
            id: el.id || null,
            role: el.getAttribute('role') || el.tagName.toLowerCase(),
            title: el.getAttribute('aria-label') || el.querySelector('h1, h2, h3, h4, [class*="title"]')?.innerText?.trim() || null,
            visible: vp.visible,
            in_viewport: vp.in_viewport,
            visible_viewports: vp.visible_viewports,
            viewport_visibility: vp.viewport_visibility,
            selector: getBestSelector(el),
            bounding_box: getRect(el)
        });
    });

    // 8. Headings
    const headings = [];
    document.querySelectorAll('h1, h2, h3, h4, h5, h6').forEach(el => {
        if (isVisible(el) && el.innerText.trim()) {
            headings.push({
                level: el.tagName.toLowerCase(),
                text: el.innerText.trim()
            });
        }
    });

    // 9. Images
    const images = [];
    document.querySelectorAll('img[src], svg[aria-label]').forEach(el => {
        const vp = makeVpProps(el);
        images.push({
            alt: el.getAttribute('alt') || el.getAttribute('aria-label') || null,
            src: el.src || el.getAttribute('src') || null,
            visible: vp.visible,
            in_viewport: vp.in_viewport,
            visible_viewports: vp.visible_viewports,
            viewport_visibility: vp.viewport_visibility,
            selector: getBestSelector(el),
            bounding_box: getRect(el)
        });
    });

    // 10. Page Meta & Body Text
    const metaDescription = document.querySelector('meta[name="description"]')?.getAttribute('content') || null;
    
    // Extract clean body text preview
    const bodyClone = (document.querySelector('main') || document.body).cloneNode(true);
    const nonTextElements = bodyClone.querySelectorAll('script, style, noscript, svg, nav, footer');
    nonTextElements.forEach(n => n.remove());
    const rawBodyText = bodyClone.innerText || '';
    const bodyTextPreview = rawBodyText.replace(/\\s+/g, ' ').trim().slice(0, 1500);

    return {
        page: {
            url: window.location.href,
            title: document.title,
            description: metaDescription
        },
        elements: {
            buttons,
            inputs,
            links,
            textareas,
            selects,
            forms,
            dialogs,
            headings,
            images
        },
        text: {
            headings,
            body_text_preview: bodyTextPreview
        }
    };
}"""


def normalize_url(raw_url: str, base_url: str) -> Optional[str]:
    """
    Resolves relative URLs, removes fragment, cleans tracking query params,
    and normalizes the URL string for deterministic visited set tracking.
    """
    if not raw_url or raw_url.strip().startswith(("javascript:", "mailto:", "tel:", "data:", "#")):
        return None

    try:
        resolved = urljoin(base_url, raw_url.strip())
        parsed = urlparse(resolved)

        if parsed.scheme not in ("http", "https"):
            return None

        # Filter tracking query parameters
        filtered_query = []
        if parsed.query:
            query_pairs = parse_qsl(parsed.query, keep_blank_values=False)
            filtered_query = [(k, v) for k, v in query_pairs if k.lower() not in TRACKING_QUERY_PARAMS]

        clean_query = urlencode(filtered_query)
        clean_path = parsed.path or "/"
        # Normalize duplicate slashes
        while "//" in clean_path:
            clean_path = clean_path.replace("//", "/")
        # Strip trailing slash unless root path
        if len(clean_path) > 1 and clean_path.endswith("/"):
            clean_path = clean_path[:-1]

        normalized = urlunparse((
            parsed.scheme.lower(),
            parsed.netloc.lower(),
            clean_path,
            "",  # params
            clean_query,
            ""   # fragment stripped
        ))
        return normalized
    except Exception:
        return None


def is_internal_same_domain(
    target_url: str,
    base_domain: str,
    excluded_patterns: Optional[List[str]] = None
) -> bool:
    """Checks if a URL belongs to the same domain and is not in excluded patterns."""
    try:
        parsed = urlparse(target_url)
        target_netloc = parsed.netloc.lower().split(":")[0]
        base_netloc = base_domain.lower().split(":")[0]

        # Check domain match (including www variants)
        if target_netloc != base_netloc and not target_netloc.endswith("." + base_netloc):
            if not base_netloc.endswith("." + target_netloc):
                return False

        # Check path exclusions
        path_lower = parsed.path.lower()
        if excluded_patterns:
            for pattern in excluded_patterns:
                pattern_lower = pattern.lower()
                if pattern_lower in path_lower or fnmatch.fnmatch(path_lower, pattern_lower):
                    return False

        return True
    except Exception:
        return False


CHECK_VIEWPORT_ELEMENTS_SCRIPT = """(items) => {
    const isVisible = (el) => {
        if (!el) return false;
        const style = window.getComputedStyle(el);
        if (style.display === 'none' || style.visibility === 'hidden' || style.opacity === '0') return false;
        const rect = el.getBoundingClientRect();
        return rect.width > 0 && rect.height > 0;
    };
    const isInViewport = (el) => {
        if (!isVisible(el)) return false;
        const r = el.getBoundingClientRect();
        return r.bottom > 0 && r.right > 0 && r.top < window.innerHeight && r.left < window.innerWidth;
    };
    const results = {};
    for (const item of items) {
        let el = null;
        if (item.id) {
            el = document.getElementById(item.id);
        }
        if (!el && item.selector) {
            try { el = document.querySelector(item.selector); } catch(e) {}
        }
        const vis = isVisible(el);
        results[item.key] = {
            visible: vis,
            in_viewport: vis ? isInViewport(el) : false
        };
    }
    return results;
}"""


async def inspect_multi_viewports(
    page: Page,
    raw_data: Dict[str, Any],
    base_viewport: Dict[str, int]
) -> None:
    """
    Evaluates visibility of discovered elements across canonical viewports:
    Desktop (1280x800), Tablet (768x1024), and Mobile (375x667).
    Populates visible_viewports, viewport_visibility, and viewports_summary.
    """
    elements_dict = raw_data.get("elements", {})
    categories = ["buttons", "inputs", "links", "textareas", "selects", "forms", "dialogs", "images"]

    # Gather items to check
    items_to_check = []
    for cat in categories:
        for idx, el in enumerate(elements_dict.get(cat, [])):
            key = f"{cat}_{idx}"
            items_to_check.append({
                "key": key,
                "category": cat,
                "index": idx,
                "id": el.get("id"),
                "selector": el.get("selector"),
            })

    if items_to_check:
        # Sweep through secondary canonical viewports
        target_viewports = ["tablet", "mobile"]
        for vp_name in target_viewports:
            vp_dims = FIXED_VIEWPORTS[vp_name]
            try:
                await page.set_viewport_size(vp_dims)
                await page.wait_for_timeout(150)  # allow CSS @media reflow
                res_map = await page.evaluate(CHECK_VIEWPORT_ELEMENTS_SCRIPT, items_to_check)

                for item in items_to_check:
                    key = item["key"]
                    cat = item["category"]
                    idx = item["index"]
                    res = res_map.get(key, {})
                    is_vis = res.get("visible", False)
                    el_obj = elements_dict[cat][idx]
                    el_obj["viewport_visibility"][vp_name] = is_vis
                    if is_vis and vp_name not in el_obj["visible_viewports"]:
                        el_obj["visible_viewports"].append(vp_name)
            except Exception as e:
                logger.warning(f"[DISCOVERY] Viewport evaluation failed for {vp_name}: {e}")

        # Restore base viewport
        try:
            await page.set_viewport_size(base_viewport)
            await page.wait_for_timeout(100)
        except Exception:
            pass

    # Compute ViewportSummary
    desktop_only = []
    tablet_only = []
    mobile_only = []
    all_viewports = []

    for cat in categories:
        for el in elements_dict.get(cat, []):
            vps = set(el.get("visible_viewports", []))
            sel = el.get("selector", "")
            if vps == {"desktop"}:
                desktop_only.append(sel)
            elif vps == {"mobile"}:
                mobile_only.append(sel)
            elif vps == {"tablet"}:
                tablet_only.append(sel)
            elif vps == {"desktop", "tablet", "mobile"}:
                all_viewports.append(sel)

    elements_dict["viewports_summary"] = {
        "desktop_only_count": len(desktop_only),
        "tablet_only_count": len(tablet_only),
        "mobile_only_count": len(mobile_only),
        "all_viewports_count": len(all_viewports),
        "desktop_only_selectors": desktop_only[:20],
        "mobile_only_selectors": mobile_only[:20],
        "tablet_only_selectors": tablet_only[:20],
    }


async def discover_page_in_context(
    page: Page,
    url: str,
    state_info: Optional[StateInfo] = None,
    viewport_width: int = 1280,
    viewport_height: int = 800,
    timeout_ms: int = 30000,
    settle_ms: int = 1500,
    save_to_storage: bool = False
) -> DiscoveryResult:
    """
    Discovers a single page within an existing Playwright Page / Context.
    """
    console_errors: List[ConsoleMessage] = []
    failed_requests: List[FailedRequest] = []

    # Attach listeners
    def handle_pageerror(err):
        console_errors.append(ConsoleMessage(type="uncaught_exception", text=str(err)))

    def handle_console(msg):
        if msg.type in ["error", "warning"]:
            console_errors.append(ConsoleMessage(type=msg.type, text=msg.text, location=msg.location))

    def handle_reqfailed(req):
        failed_requests.append(FailedRequest(url=req.url, method=req.method, failure=req.failure))

    def handle_response(res):
        if res.status >= 400:
            failed_requests.append(FailedRequest(
                url=res.url,
                method=res.request.method,
                status=res.status,
                status_text=res.status_text
            ))

    page.on("pageerror", handle_pageerror)
    page.on("console", handle_console)
    page.on("requestfailed", handle_reqfailed)
    page.on("response", handle_response)

    try:
        await page.goto(url, wait_until="networkidle", timeout=timeout_ms)
    except Exception:
        await page.goto(url, wait_until="domcontentloaded", timeout=15000)

    if settle_ms > 0:
        await page.wait_for_timeout(settle_ms)

    raw_data = await page.evaluate(DOM_EXTRACTION_SCRIPT)

    # Detach listeners to prevent accumulation
    page.remove_listener("pageerror", handle_pageerror)
    page.remove_listener("console", handle_console)
    page.remove_listener("requestfailed", handle_reqfailed)
    page.remove_listener("response", handle_response)

    # Multi-viewport sweep: test visibility across Desktop, Tablet, and Mobile
    base_viewport = {"width": viewport_width, "height": viewport_height}
    await inspect_multi_viewports(page, raw_data, base_viewport)

    # Enrich Page Info
    current_url = page.url
    parsed_current = urlparse(current_url)
    raw_data["page"]["url"] = current_url
    raw_data["page"]["path"] = parsed_current.path or "/"
    raw_data["page"]["slug"] = sanitize_page_slug(current_url)
    raw_data["page"]["viewport"] = {"width": viewport_width, "height": viewport_height}

    # State Info
    active_state = state_info or StateInfo(name="default", role="guest", is_authenticated=False)
    raw_data["state"] = active_state.model_dump()

    # Runtime Info
    raw_data["runtime"] = {
        "console_errors": [msg.model_dump() for msg in console_errors],
        "failed_requests": [req.model_dump() for req in failed_requests]
    }

    result = DiscoveryResult.model_validate(raw_data)

    # Register active state in expandable states registry
    result.states[active_state.role] = PageViewState(
        state=active_state,
        elements=result.elements,
        text=result.text
    )

    if save_to_storage:
        # Saves to storage/<domain>/discovery/pages/<slug>/index.json
        save_page_discovery(current_url, result.model_dump(), custom_slug=result.page.slug)

    return result


async def discover_page(
    url: str,
    state_info: Optional[StateInfo] = None,
    headless: Optional[bool] = None,
    viewport_width: int = 1280,
    viewport_height: int = 800,
    timeout_ms: int = 30000,
    settle_ms: int = 1500,
    save_to_storage: bool = False,
    storage_state: Optional[str] = None
) -> DiscoveryResult:
    """
    Stand-alone async single page discovery.

    `storage_state` is a path to a Playwright storage state file (cookies + localStorage)
    saved by a passing test before its browser closed. Pages only reachable behind a login
    (e.g. a dashboard) can only be discovered by reusing that authenticated session —
    without it, the fresh context would just be redirected back to the login page.
    """
    run_headless = is_headless(override=headless)
    context_kwargs = {
        "viewport": {"width": viewport_width, "height": viewport_height},
        "user_agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    }
    if storage_state and Path(storage_state).exists():
        context_kwargs["storage_state"] = storage_state
        logger.info(f"[DISCOVERY] Reusing authenticated session from: {storage_state}")

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=run_headless)
        context = await browser.new_context(**context_kwargs)
        page = await context.new_page()
        result = await discover_page_in_context(
            page=page,
            url=url,
            state_info=state_info,
            viewport_width=viewport_width,
            viewport_height=viewport_height,
            timeout_ms=timeout_ms,
            settle_ms=settle_ms,
            save_to_storage=save_to_storage
        )
        await browser.close()
    return result


def discover_page_sync(
    url: str,
    state_info: Optional[StateInfo] = None,
    headless: Optional[bool] = None,
    viewport_width: int = 1280,
    viewport_height: int = 800,
    timeout_ms: int = 30000,
    settle_ms: int = 1500,
    save_to_storage: bool = False,
    storage_state: Optional[str] = None
) -> DiscoveryResult:
    """Synchronous single-page discovery."""
    return asyncio.run(discover_page(
        url=url,
        state_info=state_info,
        headless=headless,
        viewport_width=viewport_width,
        viewport_height=viewport_height,
        timeout_ms=timeout_ms,
        settle_ms=settle_ms,
        save_to_storage=save_to_storage,
        storage_state=storage_state
    ))


# --- Multi-Page Queue Discovery Engine ---

class SiteDiscoverer:
    """
    Multi-page BFS crawler that traverses internal links, manages visited set,
    and stores structured results per page in pages/<slug>/index.json.
    """

    def __init__(self, start_url: str, config: Optional[SiteDiscoveryConfig] = None):
        self.start_url = start_url
        self.config = config or SiteDiscoveryConfig()
        self.base_domain = sanitize_domain(start_url)
        self.visited: Set[str] = set()
        self.queue: deque[Tuple[str, int, Optional[str]]] = deque()  # (normalized_url, depth, discovered_from)
        self.pages: List[PageNode] = []
        self.graph: Dict[str, List[str]] = {}
        self.max_depth_reached = 0

    async def crawl(self) -> SiteDiscoveryResult:
        normalized_root = normalize_url(self.start_url, self.start_url)
        if not normalized_root:
            raise ValueError(f"Invalid start URL: {self.start_url}")

        self.queue.append((normalized_root, 0, None))
        active_state = StateInfo(
            name="default",
            role=self.config.default_role,
            is_authenticated=self.config.is_authenticated
        )

        run_headless = is_headless(override=getattr(self.config, "headless", None))
        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=run_headless)
            context = await browser.new_context(
                viewport={"width": 1280, "height": 800},
                user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
            )
            page = await context.new_page()

            while self.queue and len(self.visited) < self.config.max_pages:
                current_url, depth, parent_url = self.queue.popleft()

                if current_url in self.visited:
                    continue

                if depth > self.config.max_depth:
                    continue

                self.visited.add(current_url)
                if depth > self.max_depth_reached:
                    self.max_depth_reached = depth

                logger.info(f"[{len(self.visited)}/{self.config.max_pages}] Discovering (depth={depth}): {current_url}")

                try:
                    page_result = await discover_page_in_context(
                        page=page,
                        url=current_url,
                        state_info=active_state,
                        timeout_ms=self.config.timeout_ms,
                        settle_ms=self.config.settle_ms,
                        save_to_storage=self.config.save_to_storage
                    )
                except Exception as e:
                    logger.error(f"Failed to discover {current_url}: {e}")
                    continue

                # Extract and filter outgoing internal links
                outbound_links: List[str] = []
                for link in page_result.elements.links:
                    norm = normalize_url(link.href, current_url)
                    if not norm:
                        continue
                    if self.config.same_domain_only and not is_internal_same_domain(norm, self.base_domain, self.config.excluded_patterns):
                        continue
                    if norm not in outbound_links:
                        outbound_links.append(norm)

                    # Enqueue for crawling if unvisited
                    if norm not in self.visited and depth + 1 <= self.config.max_depth:
                        self.queue.append((norm, depth + 1, current_url))

                self.graph[current_url] = outbound_links

                slug = page_result.page.slug or sanitize_page_slug(current_url)
                node = PageNode(
                    url=current_url,
                    title=page_result.page.title,
                    slug=slug,
                    depth=depth,
                    discovered_from=parent_url,
                    page_folder=f"pages/{slug}/",
                    page_file=f"pages/{slug}/index.json",
                    element_counts={
                        "buttons": len(page_result.elements.buttons),
                        "inputs": len(page_result.elements.inputs),
                        "links": len(page_result.elements.links),
                        "textareas": len(page_result.elements.textareas),
                        "selects": len(page_result.elements.selects),
                        "forms": len(page_result.elements.forms),
                        "dialogs": len(page_result.elements.dialogs),
                        "headings": len(page_result.elements.headings),
                        "images": len(page_result.elements.images),
                    },
                    runtime_issue_count=len(page_result.runtime.console_errors) + len(page_result.runtime.failed_requests),
                    outbound_internal_links=outbound_links
                )
                self.pages.append(node)

            await browser.close()

        site_result = SiteDiscoveryResult(
            domain=self.base_domain,
            start_url=self.start_url,
            total_pages_discovered=len(self.pages),
            max_depth_reached=self.max_depth_reached,
            pages=self.pages,
            graph=self.graph
        )

        if self.config.save_to_storage:
            save_site_discovery(self.base_domain, site_result.model_dump())

        return site_result


async def discover_site(
    start_url: str,
    config: Optional[SiteDiscoveryConfig] = None
) -> SiteDiscoveryResult:
    """Asynchronous entry point for multi-page BFS discovery."""
    discoverer = SiteDiscoverer(start_url, config=config)
    return await discoverer.crawl()


def discover_site_sync(
    start_url: str,
    config: Optional[SiteDiscoveryConfig] = None
) -> SiteDiscoveryResult:
    """Synchronous entry point for multi-page BFS discovery."""
    return asyncio.run(discover_site(start_url, config=config))


def main():
    parser = argparse.ArgumentParser(description="Forge Multi-Page Browser Discovery")
    parser.add_argument("url", nargs="?", default="https://example.com", help="Starting URL to crawl")
    parser.add_argument("--max-pages", type=int, default=5, help="Maximum number of pages to discover")
    parser.add_argument("--max-depth", type=int, default=2, help="Maximum crawl depth from root")
    parser.add_argument("--role", type=str, default="guest", help="Authentication role (e.g., guest, user, admin)")
    parser.add_argument("--json", action="store_true", help="Output full JSON to stdout")
    args = parser.parse_args()

    config = SiteDiscoveryConfig(
        max_pages=args.max_pages,
        max_depth=args.max_depth,
        default_role=args.role,
        save_to_storage=True
    )

    print(f"\n[Forge Site Discovery] Starting BFS crawl from: {args.url}")
    print(f"Config: max_pages={config.max_pages}, max_depth={config.max_depth}, role='{config.default_role}'\n")

    result = discover_site_sync(args.url, config=config)

    if args.json:
        print(json.dumps(result.model_dump(), indent=2))
        return

    print("=" * 70)
    print(f"SITE DISCOVERY COMPLETE: {result.domain}")
    print(f"Total Pages Discovered: {result.total_pages_discovered}")
    print(f"Max Depth Reached:      {result.max_depth_reached}")
    print("=" * 70)

    for i, node in enumerate(result.pages, 1):
        print(f"\n[{i}] (Depth {node.depth}) {node.title}")
        print(f"    URL : {node.url}")
        print(f"    File: {node.page_file}")
        print(f"    Elements: Buttons={node.element_counts['buttons']}, Inputs={node.element_counts['inputs']}, Links={node.element_counts['links']}, Headings={node.element_counts['headings']}")
        print(f"    Internal Links Found: {len(node.outbound_internal_links)}")

    print(f"\n[Sitemap Manifest Saved]: storage/{result.domain}/discovery/sitemap.json\n")


if __name__ == "__main__":
    main()
