import logging
from typing import Any, Dict, List
from urllib.parse import urlparse

from agents.state import ForgeState

logger = logging.getLogger("forge.agent.expectation")

# Inputs whose presence marks an unauthenticated / unsubmitted form state. Once a submit
# succeeds these normally disappear, which makes their absence a reliable, destination
# independent success signal.
_CREDENTIAL_INPUT_HINTS = ("password", "passwd", "pwd")
_SUBMIT_TEXT_HINTS = ("login", "log in", "sign in", "signin", "submit", "continue", "next", "register", "sign up")


def _describe(el: Dict[str, Any], kind: str) -> str:
    label = (
        el.get("text")
        or el.get("label")
        or el.get("placeholder")
        or el.get("name")
        or el.get("id")
        or el.get("selector")
    )
    return f"{kind}: {label}"


def _visible(el: Dict[str, Any]) -> bool:
    return bool(el.get("visible", True))


def expectation_node(state: ForgeState) -> Dict[str, Any]:
    """
    EXPECTATION node: Converts raw discovery output into a catalogue of assertions that are
    actually grounded in what was observed on the page — and, just as importantly, an explicit
    list of what is NOT knowable from a single page snapshot.

    This exists because the planner/builder otherwise invent expectations ("redirects to
    /dashboard", "a Logout button appears") that the application never satisfies. Those
    produce tests that fail against a healthy app and get misreported as bugs. Everything
    here is derived deterministically from discovery — there is nothing for an LLM to guess.
    """
    disc = state.get("discovery_data") or {}
    page = disc.get("page", {}) or {}
    elements = disc.get("elements", {}) or {}

    page_url = page.get("url") or state.get("target_url", "")
    page_path = urlparse(page_url).path or "/"

    buttons = [b for b in elements.get("buttons", []) if _visible(b)]
    inputs = [i for i in elements.get("inputs", []) if _visible(i)]
    links = elements.get("links", []) or []
    forms = elements.get("forms", []) or []
    headings = elements.get("headings", []) or []

    # 1. Assertions provably true right now — safe to assert directly.
    assertable_now: List[Dict[str, Any]] = []
    if page.get("title"):
        assertable_now.append({
            "signal": "page_title",
            "value": page.get("title"),
            "describe": f"page title is {page.get('title')!r}",
        })
    for el in inputs[:10] + buttons[:10]:
        kind = "input" if el in inputs else "button"
        if el.get("selector"):
            assertable_now.append({
                "signal": "element_present",
                "selector": el.get("selector"),
                "describe": _describe(el, kind),
            })
    for h in headings[:5]:
        if h.get("text"):
            assertable_now.append({
                "signal": "heading_text",
                "value": h.get("text"),
                "describe": f"heading {h.get('text')!r}",
            })

    # Observed initial state of toggleable inputs. A precondition must never assume the
    # opposite of what was actually on the page (e.g. asserting "checkbox 1 is checked" when
    # it loads unchecked makes the test fail before it tests anything).
    initial_states: List[Dict[str, Any]] = []
    for i in inputs:
        if i.get("checked") is None or not i.get("selector"):
            continue
        initial_states.append({
            "selector": i.get("selector"),
            "type": i.get("type"),
            "checked_at_load": bool(i.get("checked")),
            "assert_initial": (
                f"expect(page.locator({i.get('selector')!r})).to_be_checked()"
                if i.get("checked")
                else f"expect(page.locator({i.get('selector')!r})).not_to_be_checked()"
            ),
        })

    # 2. Destination-independent signals that a state transition happened. These are the
    #    elements present BEFORE the action whose disappearance proves it succeeded.
    state_change_signals: List[Dict[str, Any]] = []
    for i in inputs:
        name_blob = " ".join(
            str(i.get(k) or "") for k in ("name", "id", "placeholder", "label", "type")
        ).lower()
        if any(hint in name_blob for hint in _CREDENTIAL_INPUT_HINTS) and i.get("selector"):
            state_change_signals.append({
                "signal": "element_disappears",
                "selector": i.get("selector"),
                "describe": f"the credential field {i.get('selector')} is no longer present",
                "why": "a successful submit leaves the form state, so this input should be gone",
            })
    for b in buttons:
        text_blob = str(b.get("text") or "").lower()
        if any(hint in text_blob for hint in _SUBMIT_TEXT_HINTS) and b.get("selector"):
            state_change_signals.append({
                "signal": "element_disappears",
                "selector": b.get("selector"),
                "describe": f"the submit control {b.get('selector')} is no longer present",
                "why": "a successful submit navigates away from the form",
            })

    # 3. Routes that genuinely exist. Anything not in here must never be asserted.
    known_routes = sorted({
        urlparse(l.get("href", "")).path
        for l in links
        if l.get("href") and not str(l.get("href")).startswith(("#", "mailto:", "tel:", "javascript:"))
    } - {""})

    # 4. Real submit endpoints, so a test can assert the request succeeded rather than
    #    guessing where the browser lands.
    request_endpoints = [
        {"action": f.get("action"), "method": (f.get("method") or "GET").upper(), "selector": f.get("selector")}
        for f in forms
        if f.get("action")
    ]

    # 5. What a single-page snapshot genuinely cannot tell us.
    unverified = [
        "The destination URL after a successful submit/login is UNKNOWN — no route may be asserted "
        "unless it appears in known_routes.",
        "Any element that only renders AFTER the action (dashboards, logout controls, account menus, "
        "confirmation banners) is UNKNOWN — it was never observed and must not be asserted by name.",
    ]

    # 6. Concrete, grounded recommendations assembled from the above.
    success_assertions: List[str] = []
    for sig in state_change_signals[:3]:
        success_assertions.append(
            f"expect(page.locator({sig['selector']!r})).to_have_count(0)  # {sig['describe']}"
        )
    success_assertions.append(
        "assert page.url != start_url  # capture start_url before the action; asserts movement without naming a route"
    )
    for ep in request_endpoints[:2]:
        success_assertions.append(
            f"with page.expect_response(lambda r: r.request.method == {ep['method']!r}) as resp: ...  "
            f"then assert resp.value.status < 400  # form action {ep['action']!r}"
        )

    failure_assertions = [
        "assert page.url == start_url  # a rejected submit must NOT navigate away",
    ]
    for sig in state_change_signals[:2]:
        failure_assertions.append(
            f"expect(page.locator({sig['selector']!r})).to_be_visible()  # form still present after rejection"
        )

    signals: Dict[str, Any] = {
        "observed_page": {"url": page_url, "path": page_path, "title": page.get("title")},
        "assertable_now": assertable_now,
        "initial_input_states": initial_states,
        "state_change_signals": state_change_signals,
        "known_routes": known_routes,
        "request_endpoints": request_endpoints,
        "unverified": unverified,
        "recommended_success_assertions": success_assertions,
        "recommended_failure_assertions": failure_assertions,
    }

    logger.info(
        f"[EXPECTATION] Derived assertable signals for {page_url}: "
        f"{len(assertable_now)} present-state, {len(state_change_signals)} transition, "
        f"{len(known_routes)} known route(s), {len(request_endpoints)} form endpoint(s)."
    )
    if not state_change_signals:
        logger.info(
            "[EXPECTATION] No credential/submit controls found — success must be asserted via "
            "URL change or response status only."
        )

    return {"assertable_signals": signals}
