import logging
import re
from typing import List, Tuple

logger = logging.getLogger("forge.agent.script_lint")

# Generated scripts repeatedly reach for JS/Jest-style Playwright APIs that do not exist in
# the Python sync binding. These only blow up at runtime, where the failure looks like an
# application problem rather than a broken script, so catch them at generation time.

# (pattern, replacement, description) — deterministic, behaviour-preserving repairs.
_AUTO_FIXES: List[Tuple[str, str, str]] = [
    # expect(page.url).to_equal(x) / to_be(x) -> plain assert. expect() only accepts
    # Page, Locator or APIResponse; a str raises ValueError: Unsupported type.
    (
        r"expect\(\s*page\.url\s*\)\s*\.\s*(?:to_equal|to_be|to_have_value)\(\s*([^)]+?)\s*\)",
        r"assert page.url == \1",
        "expect(page.url).to_equal(...) -> assert page.url == ...",
    ),
    # Same mistake spelled with the JS camelCase matcher.
    (
        r"expect\(\s*page\.url\s*\)\s*\.\s*(?:toEqual|toBe)\(\s*([^)]+?)\s*\)",
        r"assert page.url == \1",
        "expect(page.url).toEqual(...) -> assert page.url == ...",
    ),
    # JS camelCase methods that have snake_case equivalents in the Python binding.
    (r"\bpage\.waitForSelector\(", "page.wait_for_selector(", "waitForSelector -> wait_for_selector"),
    (r"\bpage\.waitForTimeout\(", "page.wait_for_timeout(", "waitForTimeout -> wait_for_timeout"),
    (r"\bpage\.waitForLoadState\(", "page.wait_for_load_state(", "waitForLoadState -> wait_for_load_state"),
    (r"\bpage\.waitForURL\(", "page.wait_for_url(", "waitForURL -> wait_for_url"),
    (r"\bpage\.getByRole\(", "page.get_by_role(", "getByRole -> get_by_role"),
    (r"\bpage\.getByText\(", "page.get_by_text(", "getByText -> get_by_text"),
    (r"\bpage\.getByLabel\(", "page.get_by_label(", "getByLabel -> get_by_label"),
    (r"\bpage\.getByPlaceholder\(", "page.get_by_placeholder(", "getByPlaceholder -> get_by_placeholder"),
    (r"\.isChecked\(\)", ".is_checked()", "isChecked -> is_checked"),
    (r"\.isVisible\(\)", ".is_visible()", "isVisible -> is_visible"),
    (r"\.textContent\(\)", ".text_content()", "textContent -> text_content"),
]

# Patterns that are wrong but cannot be auto-repaired safely — surfaced as warnings.
_WARNINGS: List[Tuple[str, str]] = [
    (
        r"expect\(\s*(?!page\s*\)|page\.locator|page\.get_by|page\.frame|locator|resp|response)"
        r"(?:[\"'][^\"']*[\"']|\w+\.(?:url|title|text)\b)",
        "expect() was given a plain value; it only accepts Page, Locator or APIResponse. "
        "Use a bare `assert` for plain values.",
    ),
    (r"\bawait\s+page\.", "`await` used with the synchronous Playwright API."),
    (r"\.to_equal\(", "`.to_equal()` is not a Playwright Python assertion."),
    (r"\.toBeVisible\(|\.toHaveText\(|\.toHaveURL\(", "JS-style camelCase matcher in a Python script."),
]


def lint_and_fix(code: str) -> Tuple[str, List[str], List[str]]:
    """
    Applies deterministic repairs for known-invalid Playwright Python usage.

    Returns (repaired_code, applied_fixes, remaining_warnings).
    """
    applied: List[str] = []
    fixed = code

    for pattern, replacement, description in _AUTO_FIXES:
        new_code, count = re.subn(pattern, replacement, fixed)
        if count:
            fixed = new_code
            applied.append(f"{description} (x{count})")

    warnings: List[str] = []
    for pattern, message in _WARNINGS:
        if re.search(pattern, fixed):
            warnings.append(message)

    return fixed, applied, warnings


def apply_lint(code: str, context: str) -> str:
    """Lints generated test code, logging what was repaired and what still looks wrong."""
    fixed, applied, warnings = lint_and_fix(code)

    for fix in applied:
        logger.info(f"[SCRIPT LINT] {context}: auto-repaired {fix}")
    for warning in warnings:
        logger.warning(f"[SCRIPT LINT] {context}: {warning}")

    return fixed
