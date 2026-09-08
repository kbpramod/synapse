import json
import logging
from pathlib import Path
from typing import Any, Dict
from agents.llm import get_chat_model
from agents.state import VerificationState
from langchain_core.messages import HumanMessage, SystemMessage
from storage.local import get_website_storage_dir, sanitize_domain, mirror_to_cloud

logger = logging.getLogger("forge.verification.smoke_builder")

SMOKE_BUILDER_SYSTEM_PROMPT = """You are a Principal Software Quality Engineer and Playwright Verification Test Author.
A user journey test failed with a suspected genuine application defect.
Your task is to write a MINIMAL, TARGETED SMOKE VERIFICATION TEST in Python with Playwright.

THE GOAL:
Do NOT replay the entire user journey blindly.
Instead, write a focused test script that isolates the suspected application behavior against the live application:
- Verify the target page loads and is responsive.
- Inspect console errors and network response failures (e.g. 500 Internal Server Error).
- Attempt the minimal relevant interaction sequence around the suspected failure (e.g., can the form be submitted? Does the button respond? Does an error toast/banner appear?).
- Capture concrete application behavior so we can determine if the application is genuinely broken or if the original test just had an outdated locator.

STRICT TECHNICAL CONSTRAINTS:
1. SYNCHRONOUS API ONLY:
   Use Playwright Python synchronous API ONLY.
   `from playwright.sync_api import sync_playwright, expect`
   DO NOT use `async`, `await`, or `asyncio`.
2. SELF-CONTAINED EXECUTABLE:
   Must have `if __name__ == "__main__":` block that runs the test.
3. TELEMETRY & DIAGNOSTICS:
   Attach `page.on("console", ...)` and `page.on("response", ...)` listeners to print any 5xx/4xx HTTP statuses or uncaught JS errors.
   Print diagnostic lines like:
   `[VERIFY_SMOKE] Status: Loaded URL`
   `[VERIFY_SMOKE] Element Verified: <name>`
   `[VERIFY_SMOKE] Network Error: <url> <status>`
4. NO PLACEHOLDERS: Return ONLY clean, valid, executable Python code inside a ```python ``` block.
"""


def build_smoke_verification_test_node(state: VerificationState) -> Dict[str, Any]:
    """
    BUILD SMOKE VERIFICATION TEST node:
    Generates a targeted, minimal Playwright Python smoke test script tailored to reproduce
    and isolate the suspected application defect using fresh DOM discovery evidence.
    """
    failed_test_id = state.get("failed_test_id", "test_verification")
    target_url = state.get("target_url") or "https://example.com"
    domain = state.get("target_domain") or sanitize_domain(target_url)
    failure_ctx = state.get("failure_context") or {}
    disc = state.get("discovery_data") or {}

    logger.info(f"[VERIFICATION - SMOKE BUILDER] Building minimal smoke probe for '{failed_test_id}' on {target_url}...")

    # Extract relevant DOM elements for prompt context
    elements = disc.get("elements", {})
    available_elements = {
        "buttons": [b.get("text") for b in (elements.get("buttons", []))[:15] if b.get("text")],
        "inputs": [i.get("placeholder") or i.get("name") for i in (elements.get("inputs", []))[:10]],
        "headings": [h.get("text") for h in (elements.get("headings", []))[:8] if h.get("text")],
    }

    builder_payload = {
        "target_url": target_url,
        "failed_test_id": failed_test_id,
        "failed_step": failure_ctx.get("failed_step"),
        "expected": failure_ctx.get("expected"),
        "actual": failure_ctx.get("actual"),
        "error": failure_ctx.get("error"),
        "console_errors": failure_ctx.get("console_errors", []),
        "network_errors": failure_ctx.get("network_errors", []),
        "live_discovered_elements": available_elements,
    }

    code: str = ""
    try:
        llm = get_chat_model()
        messages = [
            SystemMessage(content=SMOKE_BUILDER_SYSTEM_PROMPT),
            HumanMessage(content=f"Verification Context & Live DOM:\n{json.dumps(builder_payload, indent=2, default=str)}")
        ]
        response = llm.invoke(messages)
        content = response.content.strip()

        if "```python" in content:
            code = content.split("```python")[1].split("```")[0].strip()
        elif "```" in content:
            code = content.split("```")[1].split("```")[0].strip()
        else:
            code = content.strip()
    except Exception as llm_err:
        logger.warning(f"[VERIFICATION - SMOKE BUILDER] LLM test synthesis failed ({llm_err}). Generating fallback smoke probe.")

    # Fallback minimal verification script if LLM did not generate
    if not code or "sync_playwright" not in code:
        code = f"""import os
import sys
from playwright.sync_api import sync_playwright, expect

def test_minimal_smoke_verify():
    print("[VERIFY_SMOKE] Starting minimal smoke probe for {failed_test_id}")
    with sync_playwright() as p:
        headless = os.getenv("HEADLESS", "false").lower() in ("true", "1", "yes")
        browser = p.chromium.launch(headless=headless)
        context = browser.new_context()
        page = context.new_page()

        # Telemetry listeners
        page.on("console", lambda msg: print(f"[BROWSER_CONSOLE] {{msg.type}}: {{msg.text}}") if msg.type in ("error", "warning") else None)
        page.on("response", lambda resp: print(f"[NETWORK_ERROR] {{resp.status}} {{resp.url}}") if resp.status >= 400 else None)

        try:
            print("[VERIFY_SMOKE] Navigating to {target_url}...")
            response = page.goto("{target_url}", timeout=20000, wait_until="networkidle")
            status = response.status if response else 0
            print(f"[VERIFY_SMOKE] HTTP Status: {{status}}")
            assert status < 400, f"Target page failed to load: HTTP {{status}}"
            print(f"[VERIFY_SMOKE] Page Title: {{page.title()}}")
            print("[VERIFY_SMOKE] Application loaded successfully.")
        except Exception as e:
            print(f"[VERIFY_SMOKE_ERROR] Baseline load failed: {{e}}", file=sys.stderr)
            raise e
        finally:
            browser.close()

if __name__ == "__main__":
    test_minimal_smoke_verify()
"""

    # Persist the smoke script to storage/<domain>/tests/verification/
    site_storage = get_website_storage_dir(target_url) if target_url else Path("storage")
    verify_dir = site_storage / "tests" / "verification"
    verify_dir.mkdir(parents=True, exist_ok=True)
    script_path = verify_dir / f"{failed_test_id}_smoke_verify.py"
    script_path.write_text(code, encoding="utf-8")
    mirror_to_cloud(script_path, code, content_type="text/x-python")

    logger.info(f"[VERIFICATION - SMOKE BUILDER] Smoke verification script saved to: {script_path}")

    return {
        "smoke_test": {
            "test_id": f"{failed_test_id}_smoke_verify",
            "script_path": str(script_path),
            "test_code": code,
        }
    }
