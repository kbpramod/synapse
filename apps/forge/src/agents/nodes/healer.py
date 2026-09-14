import json
import logging
from pathlib import Path
from typing import Any, Dict, List

from agents.llm import get_chat_model
from agents.state import ForgeState, HealEvent
from langchain_core.messages import SystemMessage, HumanMessage
from db.repository import ForgeRepository


logger = logging.getLogger("forge.agent.healer")


# ---------------------------------------------------------------------------
# HEALER PROMPT
# ---------------------------------------------------------------------------
#
# Keep this intentionally small.
#
# Deterministic code should provide facts.
# The LLM should reason over those facts.
#
# IMPORTANT:
# The healer is allowed to say "no_fix".
# A healer that refuses to make an unsupported edit is behaving correctly.
#
HEALER_SYSTEM_PROMPT = """
You are the Self-Healing Test Agent for a Playwright Python test.

A test has failed. Your job is to determine whether the TEST CODE is
responsible for the failure and, ONLY when the evidence is strong enough,
propose the smallest possible repair.

You are a reasoning agent, not a test-rewrite agent.

EVIDENCE PRIORITY
1. Runtime exception / stack trace
2. Exact failed operation
3. Failure URL
4. DOM discovered at the failure location
5. Test source
6. Other telemetry

CORE RULES

1. Treat deterministic telemetry as FACT.
   Do not reinterpret it.

2. Do not assume that a different URL means an authentication redirect.
   Use navigation.auth_redirect exactly as supplied.

3. Do not invent selectors, URLs, elements, application behavior,
   or error messages.

4. A selector may only be recommended if that selector is present in
   the supplied DOM discovery data.

5. Do not change code merely because another locator exists.
   There must be evidence that the existing code caused the failure.

6. Do not change working steps or assertions without evidence.

7. Prefer the smallest possible repair.

8. Check previous healing attempts.
   Do not recommend the same unsuccessful fix again.

9. If the evidence is contradictory, incomplete, or insufficient,
   return action="no_fix".

10. "no_fix" is a valid and successful outcome.
    Never invent a diagnosis simply because the test failed.

Before proposing a fix, establish:

- What exact operation failed?
- What evidence identifies that operation?
- Does the discovered DOM support the proposed replacement?
- Would the proposed change directly address the failure?
- Has the same fix already been attempted?

If any of these cannot be answered confidently, return "no_fix".

FAILURE CLASS

Use:
- "automation_defect"
    The test code is likely wrong: bad locator, incorrect interaction,
    missing automation step, timing/code problem, etc.

- "wrong_expectation"
    The test expects behavior that the evidence shows the application
    does not provide.

- "unknown"
    The evidence is insufficient to confidently classify the failure.

OUTPUT

Return ONLY valid JSON:

{
  "action": "fix" | "no_fix",
  "failure_class": "automation_defect" | "wrong_expectation" | "unknown",
  "diagnosis": "Concise evidence-based diagnosis",
  "evidence": [
    "Specific evidence supporting the diagnosis"
  ],
  "confidence": 0.0,
  "fix_plan": "Smallest concrete repair, or empty string when no_fix",
  "preserve": "Existing behavior that must remain unchanged"
}

The confidence value must be between 0.0 and 1.0.

Do not output markdown.
Do not output explanations outside the JSON object.
"""


# ---------------------------------------------------------------------------
# HELPERS
# ---------------------------------------------------------------------------

def _safe_text(value: Any) -> str:
    """Convert arbitrary values to a safe short string."""
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    try:
        return json.dumps(value, default=str)
    except Exception:
        return str(value)


def _normalize_url(url: str) -> str:
    """Normalize URL only for comparison/logging."""
    if not url:
        return ""
    return url.rstrip("/")


def _load_discovery(
    discovery_url: str,
    target_url: str,
) -> tuple[Dict[str, Any], str]:
    """
    Recover discovery data.

    Priority:
      1. Current state is handled by caller.
      2. Disk cache.
      3. Database.

    Returns:
        (discovery_data, source_description)
    """
    disc: Dict[str, Any] = {}
    source = "missing"

    # ------------------------------------------------------------------
    # Disk
    # ------------------------------------------------------------------
    try:
        from storage.local import (
            get_discovery_storage_dir,
            get_page_folder,
        )

        lookup_urls = []

        if discovery_url:
            lookup_urls.append(discovery_url)

        if target_url and target_url not in lookup_urls:
            lookup_urls.append(target_url)

        for lookup_url in lookup_urls:
            try:
                disc_file = (
                    get_discovery_storage_dir(lookup_url)
                    / "discovery.json"
                )

                if disc_file.exists():
                    with open(disc_file, "r", encoding="utf-8") as f:
                        disc = json.load(f)

                    if disc:
                        return disc, f"discovery disk cache ({disc_file})"

                page_file = get_page_folder(lookup_url) / "index.json"

                if page_file.exists():
                    with open(page_file, "r", encoding="utf-8") as f:
                        disc = json.load(f)

                    if disc:
                        return disc, f"page disk cache ({page_file})"

            except Exception as exc:
                logger.debug(
                    "[HEAL:DISCOVERY] Failed disk lookup for %s: %s",
                    lookup_url,
                    exc,
                )

    except Exception as exc:
        logger.debug(
            "[HEAL:DISCOVERY] Disk discovery unavailable: %s",
            exc,
        )

    # ------------------------------------------------------------------
    # Database
    # ------------------------------------------------------------------
    try:
        lookup_urls = []

        if discovery_url:
            lookup_urls.append(discovery_url)

        if target_url and target_url not in lookup_urls:
            lookup_urls.append(target_url)

        for lookup_url in lookup_urls:
            try:
                page_rec = ForgeRepository.get_page_by_url(lookup_url)

                if page_rec and page_rec.get("metadata_json"):
                    disc = page_rec["metadata_json"]

                    if disc:
                        return (
                            disc,
                            f"database (forge.pages for {lookup_url})",
                        )

            except Exception as exc:
                logger.debug(
                    "[HEAL:DISCOVERY] Database lookup failed for %s: %s",
                    lookup_url,
                    exc,
                )

    except Exception as exc:
        logger.debug(
            "[HEAL:DISCOVERY] Database discovery unavailable: %s",
            exc,
        )

    return {}, source


def _extract_elements(
    discovery_data: Dict[str, Any],
) -> Dict[str, List[Dict[str, Any]]]:
    """Extract raw DOM discovery collections."""
    raw_elements = discovery_data.get("elements") or {}

    return {
        "buttons": raw_elements.get("buttons") or [],
        "inputs": raw_elements.get("inputs") or [],
        "links": raw_elements.get("links") or [],
        "selects": raw_elements.get("selects") or [],
        "forms": raw_elements.get("forms") or [],
    }


def _element_text(element: Dict[str, Any]) -> str:
    """Create a searchable representation of one discovered element."""
    fields = (
        "selector",
        "id",
        "name",
        "text",
        "placeholder",
        "forge_id",
        "role",
        "aria_label",
        "href",
        "type",
    )

    return " ".join(
        str(element.get(field) or "")
        for field in fields
    ).lower()


def _extract_failure_evidence(
    execution_result: Dict[str, Any],
) -> Dict[str, Any]:
    """
    Extract the most useful failure information.

    This is deliberately conservative.
    We do not attempt to infer a diagnosis here.
    """
    error_summary = execution_result.get("error_summary") or ""
    stderr = execution_result.get("stderr") or ""

    return {
        "error_summary": error_summary,
        "stderr": stderr[-4000:],
        "exception": execution_result.get("exception"),
        "failure_url": execution_result.get("failure_url"),
        "exit_code": execution_result.get("exit_code"),
        "passed": execution_result.get("passed"),
    }


def _build_dom_for_llm(
    raw_elements: Dict[str, List[Dict[str, Any]]],
    failure_evidence: Dict[str, Any],
    max_buttons: int = 40,
    max_inputs: int = 25,
    max_links: int = 50,
    max_selects: int = 10,
) -> Dict[str, List[Dict[str, Any]]]:
    """
    Prepare DOM evidence for the LLM.

    Unlike the previous implementation, this does NOT rank elements
    using the test title/scenario keywords.

    We keep the DOM grounded and predictable.
    """

    def simplify(
        element: Dict[str, Any],
        fields: List[str],
    ) -> Dict[str, Any]:
        return {
            field: element.get(field)
            for field in fields
            if element.get(field) is not None
        }

    # ------------------------------------------------------------------
    # If the runtime error contains a literal selector/text, put matching
    # elements first. This is evidence-based ranking.
    # ------------------------------------------------------------------
    error_text = " ".join(
        [
            _safe_text(failure_evidence.get("error_summary")),
            _safe_text(failure_evidence.get("stderr")),
        ]
    ).lower()

    def relevance_score(element: Dict[str, Any]) -> int:
        searchable = _element_text(element)

        score = 0

        # Only exact fragments already present in the error are considered.
        for token in (
            element.get("selector"),
            element.get("id"),
            element.get("name"),
            element.get("text"),
            element.get("href"),
        ):
            if token and str(token).lower() in error_text:
                score += 10

        # Don't invent semantic relevance.
        if searchable and searchable in error_text:
            score += 5

        return score

    def prepare(
        elements: List[Dict[str, Any]],
        limit: int,
        fields: List[str],
    ) -> List[Dict[str, Any]]:
        ranked = sorted(
            elements,
            key=relevance_score,
            reverse=True,
        )

        return [
            simplify(element, fields)
            for element in ranked[:limit]
        ]

    return {
        "buttons": prepare(
            raw_elements["buttons"],
            max_buttons,
            [
                "text",
                "selector",
                "id",
                "forge_id",
                "role",
                "aria_label",
            ],
        ),
        "inputs": prepare(
            raw_elements["inputs"],
            max_inputs,
            [
                "name",
                "placeholder",
                "selector",
                "id",
                "forge_id",
                "type",
                "label",
            ],
        ),
        "links": prepare(
            raw_elements["links"],
            max_links,
            [
                "text",
                "selector",
                "id",
                "forge_id",
                "href",
                "role",
                "aria_label",
            ],
        ),
        "selects": prepare(
            raw_elements["selects"],
            max_selects,
            [
                "name",
                "selector",
                "id",
                "forge_id",
                "options",
            ],
        ),
    }


def _selector_exists_in_dom(
    fix_plan: str,
    available_elements: Dict[str, List[Dict[str, Any]]],
) -> bool:
    """
    Conservative grounding check.

    If the proposed fix explicitly mentions a selector that looks like
    a CSS selector, verify that it occurs in discovery.

    This is not intended to understand every possible LLM sentence.
    It is a safety guard against obvious invented selectors.
    """

    if not fix_plan:
        return True

    selectors = set()

    for collection in available_elements.values():
        for element in collection:
            selector = element.get("selector")
            element_id = element.get("id")
            href = element.get("href")

            if selector:
                selectors.add(str(selector))

            if element_id:
                selectors.add(f"#{element_id}")

            if href:
                selectors.add(str(href))

    # Extract common CSS-like strings from the proposed plan.
    candidates = []

    # #foo
    import re

    candidates.extend(
        re.findall(r"#[A-Za-z0-9_-]+", fix_plan)
    )

    # [href="..."], [name="..."], etc.
    candidates.extend(
        re.findall(r"\[[^\]]+\]", fix_plan)
    )

    # locator("...")
    candidates.extend(
        re.findall(
            r"""(?:locator|get_by_text|get_by_role|get_by_label|get_by_placeholder)
                \(\s*['"]([^'"]+)['"]""",
            fix_plan,
            flags=re.IGNORECASE | re.VERBOSE,
        )
    )

    for candidate in candidates:
        candidate = candidate.strip()

        if not candidate:
            continue

        if candidate in selectors:
            continue

        # get_by_text("foo") may not correspond to a CSS selector.
        # Check whether exact visible text exists.
        if any(
            candidate == str(element.get("text") or "")
            for collection in available_elements.values()
            for element in collection
        ):
            continue

        # If it looks like a CSS selector and wasn't discovered,
        # reject it.
        looks_like_selector = (
            candidate.startswith("#")
            or candidate.startswith(".")
            or candidate.startswith("[")
            or candidate.startswith("/")
            or ">" in candidate
            or "*" in candidate
        )

        if looks_like_selector:
            return False

    return True


def _previous_fix_already_attempted(
    fix_plan: str,
    healing_history: List[Dict[str, Any]],
) -> bool:
    """Detect obvious repeated fixes."""
    if not fix_plan:
        return False

    normalized = " ".join(fix_plan.lower().split())

    for event in healing_history[-5:]:
        previous = " ".join(
            str(event.get("fix_plan") or "").lower().split()
        )

        if previous and previous == normalized:
            return True

    return False


def _safe_no_fix(
    *,
    diagnosis: str,
    evidence: List[str],
    preserve: str = "Do not modify the test until stronger evidence is available.",
) -> Dict[str, Any]:
    """Return a conservative no-fix result."""
    return {
        "action": "no_fix",
        "failure_class": "unknown",
        "diagnosis": diagnosis,
        "evidence": evidence,
        "confidence": 0.0,
        "fix_plan": "",
        "preserve": preserve,
    }


def _parse_llm_result(content: str) -> Dict[str, Any]:
    """Parse and minimally validate the LLM JSON."""
    content = content.strip()

    if content.startswith("```"):
        lines = content.splitlines()

        if lines and lines[0].startswith("```"):
            lines = lines[1:]

        if lines and lines[-1].startswith("```"):
            lines = lines[:-1]

        content = "\n".join(lines).strip()

    result = json.loads(content)

    if not isinstance(result, dict):
        raise ValueError("Healer response is not a JSON object")

    action = str(result.get("action") or "").strip().lower()

    if action not in ("fix", "no_fix"):
        raise ValueError(
            f"Invalid healer action: {action!r}"
        )

    failure_class = str(
        result.get("failure_class") or "unknown"
    ).strip().lower()

    if failure_class not in (
        "automation_defect",
        "wrong_expectation",
        "unknown",
    ):
        failure_class = "unknown"

    try:
        confidence = float(result.get("confidence", 0.0))
    except Exception:
        confidence = 0.0

    confidence = max(0.0, min(1.0, confidence))

    evidence = result.get("evidence") or []

    if isinstance(evidence, str):
        evidence = [evidence]

    if not isinstance(evidence, list):
        evidence = [str(evidence)]

    return {
        "action": action,
        "failure_class": failure_class,
        "diagnosis": str(
            result.get("diagnosis") or ""
        ).strip(),
        "evidence": [
            str(item)
            for item in evidence
            if item is not None
        ],
        "confidence": confidence,
        "fix_plan": str(
            result.get("fix_plan") or ""
        ).strip(),
        "preserve": str(
            result.get("preserve") or ""
        ).strip(),
    }


# ---------------------------------------------------------------------------
# MAIN HEALER NODE
# ---------------------------------------------------------------------------

def healer_node(state: ForgeState) -> Dict[str, Any]:
    """
    Diagnose a failed test and produce an evidence-grounded healing plan.

    Important design rule:
        The Healer does NOT have to produce a fix.

    It may return:
        action = "fix"
    or:
        action = "no_fix"

    The rest of the pipeline should respect that decision.
    """

    current_test = state.get("current_test") or {}
    heal_attempt = state.get("heal_attempt", 0)
    healing_history = list(
        state.get("healing_history", [])
    )

    execution_result = (
        state.get("execution_result") or {}
    )

    analysis = state.get("analysis") or {}
    failure_context = (
        state.get("failure_context") or {}
    )

    target_url = (
        state.get("target_url")
        or current_test.get("page_url")
        or current_test.get("target_url")
        or ""
    )

    test_id = (
        current_test.get("id")
        or current_test.get("test_id")
        or "unknown_test"
    )

    logger.info("=" * 70)
    logger.info(
        "[HEAL] >>> ENTERING HEALER NODE for '%s' "
        "(Attempt #%s) <<<",
        test_id,
        heal_attempt + 1,
    )
    logger.info("=" * 70)

    # ------------------------------------------------------------------
    # 1. FAILURE / NAVIGATION FACTS
    # ------------------------------------------------------------------

    failure_url = (
        execution_result.get("failure_url")
        or failure_context.get("failure_url")
        or ""
    )

    navigation = (
        failure_context.get("navigation")
        or {}
    )

    # These are FACTS from upstream.
    # Do not infer them again inside the LLM.
    auth_redirect = bool(
        navigation.get("auth_redirect", False)
    )

    expected_navigation = bool(
        navigation.get("expected", True)
    )

    discovery_url = (
        failure_context.get("discovery_url")
        or failure_url
        or target_url
    )

    # ------------------------------------------------------------------
    # 2. DISCOVERY
    # ------------------------------------------------------------------

    discovery_data = (
        state.get("discovery_data") or {}
    )

    discovery_source = (
        "state.discovery_data"
        if discovery_data
        else "missing"
    )

    if not discovery_data or not discovery_data.get("elements"):
        discovery_data, discovery_source = _load_discovery(
            discovery_url,
            target_url,
        )

    discovery_page = (
        discovery_data.get("page") or {}
    )

    discovery_snapshot_url = (
        discovery_page.get("url")
        or discovery_data.get("url")
        or discovery_url
        or ""
    )

    discovery_title = (
        discovery_page.get("title")
        or discovery_data.get("title")
        or ""
    )

    raw_elements = _extract_elements(
        discovery_data
    )

    body_text_preview = (
        (discovery_data.get("text") or {})
        .get("body_text_preview")
        or ""
    )[:600]

    headings = (
        (discovery_data.get("text") or {})
        .get("headings")
        or discovery_data.get("headings")
        or []
    )

    page_headings = [
        h.get("text")
        if isinstance(h, dict)
        else str(h)
        for h in headings[:10]
    ]

    logger.info(
        "[HEAL:DISCOVERY] Source: %s",
        discovery_source,
    )

    logger.info(
        "[HEAL:DISCOVERY] Snapshot URL: '%s'",
        discovery_snapshot_url,
    )

    logger.info(
        "[HEAL:DISCOVERY] Snapshot Title: '%s'",
        discovery_title,
    )

    logger.info(
        "[HEAL:DISCOVERY] Elements: "
        "buttons=%d inputs=%d links=%d selects=%d forms=%d",
        len(raw_elements["buttons"]),
        len(raw_elements["inputs"]),
        len(raw_elements["links"]),
        len(raw_elements["selects"]),
        len(raw_elements["forms"]),
    )

    # IMPORTANT:
    # Do NOT emit "URL MISMATCH = redirect".
    #
    # The discovery page is allowed to be the failure destination.
    #
    if (
        discovery_snapshot_url
        and discovery_url
        and _normalize_url(discovery_snapshot_url)
        != _normalize_url(discovery_url)
    ):
        logger.info(
            "[HEAL:DISCOVERY] Snapshot URL differs from requested "
            "discovery URL: '%s' -> '%s'",
            discovery_url,
            discovery_snapshot_url,
        )

    # ------------------------------------------------------------------
    # 3. FAILURE EVIDENCE
    # ------------------------------------------------------------------

    failure_evidence = _extract_failure_evidence(
        execution_result
    )

    visible_errors = (
        execution_result.get("visible_errors")
        or failure_context.get("visible_errors")
        or []
    )

    logger.info(
        "[HEAL:EVIDENCE] Error Summary: '%s'",
        failure_evidence["error_summary"],
    )

    logger.info(
        "[HEAL:EVIDENCE] Failure URL: '%s'",
        failure_url,
    )

    logger.info(
        "[HEAL:EVIDENCE] Auth Redirect: %s | Expected Navigation: %s",
        auth_redirect,
        expected_navigation,
    )

    logger.info(
        "[HEAL:EVIDENCE] Visible Errors: %s",
        visible_errors,
    )

    # ------------------------------------------------------------------
    # 4. PREPARE TEST CODE (ALWAYS ACTIVE CURRENT VERSION)
    # ------------------------------------------------------------------

    test_file_path = state.get("test_file_path")
    test_code = ""

    # PRIMARY SOURCE OF TRUTH: Always read the exact active test script from disk
    # that Runner just executed. Never reason over a stale or pre-edit version.
    if test_file_path and Path(test_file_path).exists():
        try:
            with open(test_file_path, "r", encoding="utf-8") as f:
                test_code = f.read()
            logger.info("[HEAL] Loaded current active test code from disk: %s (%d chars)", test_file_path, len(test_code))
        except Exception as exc:
            logger.warning("[HEAL] Could not read active test file %s: %s", test_file_path, exc)

    if not test_code:
        test_code = state.get("test_code") or ""

    # Keep the full source when reasonably small.
    # Otherwise keep the tail where the test body usually lives.
    if len(test_code) > 12000:
        test_code_for_llm = test_code[-12000:]
    else:
        test_code_for_llm = test_code

    # ------------------------------------------------------------------
    # 5. DOM EVIDENCE
    # ------------------------------------------------------------------

    available_elements = _build_dom_for_llm(
        raw_elements,
        failure_evidence,
    )

    logger.info(
        "[HEAL:DOM] Sending %d buttons, %d inputs, "
        "%d links, %d selects",
        len(available_elements["buttons"]),
        len(available_elements["inputs"]),
        len(available_elements["links"]),
        len(available_elements["selects"]),
    )

    # ------------------------------------------------------------------
    # 6. PREVIOUS ATTEMPTS
    # ------------------------------------------------------------------

    previous_attempts = healing_history[-5:]

    # Strip irrelevant giant fields before sending history to LLM.
    history_for_llm = []

    for attempt in previous_attempts:
        history_for_llm.append(
            {
                "attempt": attempt.get("attempt"),
                "failure_class": attempt.get(
                    "failure_class"
                ),
                "diagnosis": attempt.get(
                    "diagnosis"
                ),
                "fix_plan": attempt.get(
                    "fix_plan"
                ),
                "preserve": attempt.get(
                    "preserve"
                ),
            }
        )

    # ------------------------------------------------------------------
    # 7. OTHER TELEMETRY
    # ------------------------------------------------------------------

    storage_state_available = None

    try:
        from storage.local import (
            get_website_storage_dir,
        )

        if target_url:
            site_storage = (
                get_website_storage_dir(target_url)
            )

            tests_dir = site_storage / "tests"

            if tests_dir.exists():
                session_files = list(
                    tests_dir.glob(
                        "**/*.storage_state.json"
                    )
                )

                if session_files:
                    storage_state_available = str(
                        session_files[0]
                        .resolve()
                    ).replace("\\", "/")

    except Exception as exc:
        logger.debug(
            "[HEAL] Storage-state lookup failed: %s",
            exc,
        )

    available_accounts = []

    try:
        website_id = (
            state.get("website_id")
            or current_test.get("website_id")
        )

        if website_id:
            accounts = (
                ForgeRepository
                .get_credentials_for_website(
                    int(website_id)
                )
            )
        else:
            accounts = (
                ForgeRepository
                .get_credentials_for_url(
                    target_url
                )
            )

        available_accounts = [
            {
                "username": account.get(
                    "username"
                ),
                "role": account.get("role"),
            }
            for account in accounts
        ]

    except Exception as exc:
        logger.debug(
            "[HEAL] Account lookup failed: %s",
            exc,
        )

    # ------------------------------------------------------------------
    # 8. STRUCTURED PAYLOAD
    # ------------------------------------------------------------------
    #
    # Important:
    # We deliberately do NOT feed the LLM:
    #
    # - scenario keyword rankings
    # - arbitrary heuristic conclusions
    # - URL mismatch warnings
    # - "this often means auth" messages
    #
    # We give it evidence.
    #

    healer_payload = {
        "test": {
            "test_id": test_id,
            "intent": (
                current_test.get("intent")
                or current_test.get("goal")
                or current_test.get("description")
            ),
            "expected_outcomes": (
                current_test.get("expected")
                or [
                    current_test.get(
                        "expected_outcome"
                    )
                ]
            ),
            "current_test_code": test_code_for_llm,
            "source_code": test_code_for_llm,
        },

        "failure": {
            "error_summary": failure_evidence[
                "error_summary"
            ],
            "stderr": failure_evidence[
                "stderr"
            ],
            "exception": failure_evidence[
                "exception"
            ],
            "exit_code": failure_evidence[
                "exit_code"
            ],
            "passed": failure_evidence[
                "passed"
            ],
        },

        "navigation": {
            "target_url": target_url,
            "failure_url": failure_url,
            "discovery_url": discovery_url,
            "expected": expected_navigation,
            "auth_redirect": auth_redirect,
        },

        "page": {
            "discovery_snapshot_url": (
                discovery_snapshot_url
            ),
            "title": discovery_title,
            "headings": page_headings,
            "body_text_preview": body_text_preview,
            "visible_errors": visible_errors,
        },

        "dom": available_elements,

        "session": {
            "storage_state_available": (
                storage_state_available
            ),
            "available_accounts": (
                available_accounts
            ),
        },

        "previous_heal_attempts": history_for_llm,

        "analyzer": {
            "suggested_fix": analysis.get(
                "suggested_fix"
            ),
            "diagnosis": analysis.get(
                "diagnosis"
            ),
        },
    }

    payload_json = json.dumps(
        healer_payload,
        indent=2,
        default=str,
    )

    logger.info(
        "[HEAL:LLM_DISPATCH] Payload size: %d chars",
        len(payload_json),
    )

    # ------------------------------------------------------------------
    # 9. INVOKE LLM
    # ------------------------------------------------------------------

    try:
        llm = get_chat_model()

        messages = [
            SystemMessage(
                content=HEALER_SYSTEM_PROMPT
            ),
            HumanMessage(
                content=(
                    "Analyze this failed test.\n\n"
                    "Failure evidence:\n"
                    f"{payload_json}"
                )
            ),
        ]

        response = llm.invoke(messages)

        content = (
            response.content or ""
        ).strip()

        heal_result = _parse_llm_result(
            content
        )

    except Exception as exc:
        # --------------------------------------------------------------
        # IMPORTANT:
        # If the LLM fails, do NOT invent a repair.
        # --------------------------------------------------------------

        logger.warning(
            "[HEAL:LLM] Healer LLM failed: %s",
            exc,
        )

        heal_result = _safe_no_fix(
            diagnosis=(
                "The healer could not reliably analyze "
                "the failure."
            ),
            evidence=[
                str(
                    failure_evidence.get(
                        "error_summary"
                    )
                    or "No error summary available."
                )
            ],
        )

    # ------------------------------------------------------------------
    # 10. SAFETY VALIDATION OF LLM RESULT
    # ------------------------------------------------------------------

    action = heal_result["action"]
    failure_class = heal_result[
        "failure_class"
    ]
    diagnosis = heal_result["diagnosis"]
    evidence = heal_result["evidence"]
    confidence = heal_result["confidence"]
    fix_plan = heal_result["fix_plan"]
    preserve = heal_result["preserve"]

    # --------------------------------------------------------------
    # No diagnosis = no fix
    # --------------------------------------------------------------
    if not diagnosis:
        heal_result = _safe_no_fix(
            diagnosis=(
                "The healer did not provide a "
                "reliable diagnosis."
            ),
            evidence=evidence,
        )

        action = "no_fix"
        failure_class = "unknown"
        diagnosis = heal_result["diagnosis"]
        evidence = heal_result["evidence"]
        confidence = heal_result["confidence"]
        fix_plan = ""
        preserve = heal_result["preserve"]

    # --------------------------------------------------------------
    # Fix without a fix plan = no fix
    # --------------------------------------------------------------
    if action == "fix" and not fix_plan:
        logger.warning(
            "[HEAL:GUARD] LLM requested fix without "
            "a concrete fix plan. Converting to no_fix."
        )

        heal_result = _safe_no_fix(
            diagnosis=diagnosis,
            evidence=evidence,
            preserve=preserve,
        )

        action = "no_fix"
        failure_class = "unknown"
        fix_plan = ""
        confidence = heal_result["confidence"]

    # --------------------------------------------------------------
    # Reject invented DOM selectors
    # --------------------------------------------------------------
    if action == "fix":
        if not _selector_exists_in_dom(
            fix_plan,
            available_elements,
        ):
            logger.warning(
                "[HEAL:GUARD] Proposed fix contains "
                "a selector not present in discovery. "
                "Converting to no_fix."
            )

            heal_result = _safe_no_fix(
                diagnosis=(
                    diagnosis
                    + " Proposed repair could not be "
                    "grounded in discovered DOM."
                ),
                evidence=evidence,
                preserve=preserve,
            )

            action = "no_fix"
            failure_class = "unknown"
            fix_plan = ""
            confidence = heal_result[
                "confidence"
            ]

    # --------------------------------------------------------------
    # Reject repeated fixes
    # --------------------------------------------------------------
    if action == "fix":
        if _previous_fix_already_attempted(
            fix_plan,
            healing_history,
        ):
            logger.warning(
                "[HEAL:GUARD] Proposed fix was already "
                "attempted. Converting to no_fix."
            )

            heal_result = _safe_no_fix(
                diagnosis=(
                    diagnosis
                    + " The proposed repair was already "
                    "attempted in a previous healing cycle."
                ),
                evidence=evidence,
                preserve=preserve,
            )

            action = "no_fix"
            failure_class = "unknown"
            fix_plan = ""
            confidence = heal_result[
                "confidence"
            ]

    # --------------------------------------------------------------
    # Low confidence should not produce an edit
    # --------------------------------------------------------------
    if action == "fix" and confidence < 0.60:
        logger.info(
            "[HEAL:GUARD] Confidence %.2f is below "
            "minimum fix threshold. Returning no_fix.",
            confidence,
        )

        heal_result = _safe_no_fix(
            diagnosis=diagnosis,
            evidence=evidence,
            preserve=preserve,
        )

        action = "no_fix"
        failure_class = "unknown"
        fix_plan = ""
        confidence = heal_result[
            "confidence"
        ]

    # ------------------------------------------------------------------
    # 11. LOG FINAL DECISION
    # ------------------------------------------------------------------

    logger.info(
        "[HEAL:RESULT] Action: %s",
        action,
    )

    logger.info(
        "[HEAL:RESULT] Failure Class: %s",
        failure_class,
    )

    logger.info(
        "[HEAL:RESULT] Confidence: %.2f",
        confidence,
    )

    logger.info(
        "[HEAL:RESULT] Diagnosis: %s",
        diagnosis,
    )

    if evidence:
        logger.info(
            "[HEAL:RESULT] Evidence: %s",
            evidence,
        )

    logger.info(
        "[HEAL:RESULT] Fix Plan: %s",
        fix_plan or "(none)",
    )

    logger.info(
        "[HEAL:RESULT] Preserve: %s",
        preserve or "(none)",
    )

    # ------------------------------------------------------------------
    # 12. RECORD HEAL EVENT
    # ------------------------------------------------------------------

    heal_event: HealEvent = {
        "attempt": heal_attempt + 1,
        "test_id": test_id,
        "error_snippet": str(
            execution_result.get(
                "error_summary"
            )
            or ""
        )[:300],
        "failure_class": failure_class,
        "diagnosis": diagnosis,
        "fix_plan": fix_plan,
        "preserve": preserve,
    }

    # Add optional fields if ForgeState/HealEvent accepts them.
    #
    # We intentionally do this after constructing the base event so
    # older TypedDict definitions don't break the main flow.
    try:
        heal_event["action"] = action
        heal_event["confidence"] = confidence
        heal_event["evidence"] = evidence
    except Exception:
        pass

    healing_history.append(
        heal_event
    )

    logger.info("=" * 70)
    logger.info(
        "[HEAL] Completed Healer node for '%s'. "
        "Action=%s",
        test_id,
        action,
    )
    logger.info("=" * 70)

    # ------------------------------------------------------------------
    # 13. RETURN STATE
    # ------------------------------------------------------------------

    return {
        "heal_attempt": heal_attempt + 1,
        "test_code": test_code,
        "healing_history": healing_history,

        "healing_plan": {
            "action": action,
            "failure_class": failure_class,
            "diagnosis": diagnosis,
            "evidence": evidence,
            "confidence": confidence,
            "fix_plan": fix_plan,
            "preserve": preserve,
        },
    }
