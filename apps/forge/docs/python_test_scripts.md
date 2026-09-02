# Python Playwright Test Scripts in Forge

Forge generates native Python test scripts (`.py`) by default using the Playwright Python API (`playwright.sync_api`).

## Why Python Test Scripts?

1. **Zero External Runtime Dependencies**: Tests run directly inside the Python virtual environment (`.venv`) using `sys.executable`, eliminating the need for `npx`, `node_modules`, or TypeScript compilation overhead.
2. **Fast, Predictable Execution**: Direct execution via Python subprocess ensures consistent execution across operating systems (Windows, Linux, macOS).
3. **Rich Debugging and Stack Traces**: Python assertion errors (e.g. from `expect(locator).to_be_visible()`) produce tracebacks that are directly parsed by the Self-Healing and Defect Analyzer nodes.
4. **Pytest and Direct Script Compatible**: Scripts can be run directly via `python <test>.py` or through `pytest`.

---

## Script Architecture

Each generated Python test script follows this resilient structure:

```python
import os
import re
import sys
from playwright.sync_api import sync_playwright, expect


def test_smoke_page_load():
    # Reads HEADLESS env var (configured dynamically by Forge)
    headless = os.getenv("HEADLESS", "true").lower() in ("true", "1", "yes")
    
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=headless)
        context = browser.new_context(viewport={"width": 1280, "height": 800})
        page = context.new_page()
        try:
            # Safe navigation
            page.goto("https://wecatchai.com/", wait_until="domcontentloaded", timeout=30000)
            
            # Assertions using Playwright's expect API
            expect(page).to_have_title(re.compile(r".*WeCatchAI.*", re.IGNORECASE))
            expect(page.get_by_role("button", name="Products")).to_be_visible()
            
            print("[TEST PASSED] Smoke Page Load & CTAs")
        finally:
            context.close()
            browser.close()


if __name__ == "__main__":
    test_smoke_page_load()
```

---

## Locators and Assertions

Forge instructs the LLM builder to generate resilient locators adhering to Playwright best practices:

- Role-based locators: `page.get_by_role("button", name="...")`
- Text-based locators: `page.get_by_text("...")`
- Label-based locators: `page.get_by_label("...")`
- Placeholder locators: `page.get_by_placeholder("...")`
- Exact element selectors: `page.locator("#submit-btn")`

Assertions use the synchronous `expect` API:
- `expect(page).to_have_title(re.compile(r"..."))`
- `expect(locator).to_be_visible()`
- `expect(locator).to_be_enabled()`
- `expect(locator).to_have_text(...)`

---

## Running Generated Tests Directly

You can execute any generated Python test directly:

```bash
# Run with Python interpreter directly
uv run python storage/wecatchai.com/tests/test_page_smoke.py

# Run with visible browser
$env:HEADLESS="false"; uv run python storage/wecatchai.com/tests/test_page_smoke.py

# Or execute with pytest
uv run pytest storage/wecatchai.com/tests/test_page_smoke.py
```
