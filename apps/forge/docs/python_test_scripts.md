# Python Playwright Test Scripts in Forge

Forge generates native Python test scripts (`.py`) by default using the Playwright Python API (`playwright.sync_api`).

## Why Python Test Scripts?

1. **Zero External Runtime Dependencies**: Tests run directly inside the Python virtual environment (`.venv`) using `sys.executable`, eliminating the need for `npx`, `node_modules`, or TypeScript compilation overhead.
2. **Fast, Predictable Execution**: Direct execution via Python subprocess ensures consistent execution across operating systems (Windows, Linux, macOS).
3. **Rich Debugging and Stack Traces**: Python assertion errors (e.g. from `expect(locator).to_be_visible()`) produce tracebacks that are directly parsed by the Self-Healing and Defect Analyzer nodes.
4. **Pytest and Direct Script Compatible**: Scripts can be run directly via `python <test>.py` or through `pytest`.

---

## Script Architecture

Each generated Python test script follows this resilient structure with built-in failure telemetry and session persistence:

```python
import json
import os
import re
import sys
from playwright.sync_api import sync_playwright, expect


def test_journey():
    headless = os.getenv("HEADLESS", "true").lower() in ("true", "1", "yes")
    
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=headless)
        
        # Session state reuse: If the page requires authentication and a saved session exists
        storage_path = os.path.splitext(os.path.abspath(__file__))[0] + ".storage_state.json"
        storage_arg = {"storage_state": storage_path} if os.path.exists(storage_path) else {}
        context = browser.new_context(viewport={"width": 1280, "height": 800}, **storage_arg)
        page = context.new_page()
        
        try:
            # Navigation
            page.goto("https://example.com/dashboard", wait_until="domcontentloaded", timeout=30000)
            
            # User interactions & assertions
            expect(page.get_by_role("heading", name="Dashboard")).to_be_visible()
            
            # Successful completion telemetry & session capture
            print(f"[FINAL_URL] {page.url}")
            context.storage_state(path=storage_path)
            print("[TEST PASSED] Successfully completed user journey")
            
        except Exception as exc:
            # Failure Telemetry: Captures actual landing URL, visible error banners, and full-page screenshot
            try:
                print(f"[FAILURE_URL] {page.url}")
                error_texts = page.locator(".error, .alert, [role='alert'], [data-test='error'], h1, h2, h3").all_inner_texts()
                clean_errors = [t.strip() for t in error_texts if t and t.strip()]
                if clean_errors:
                    print(f"[VISIBLE_ERRORS] {json.dumps(clean_errors[:5])}")
                page.screenshot(path=storage_path.replace(".storage_state.json", "_failure.png"), full_page=True)
            except Exception:
                pass
            raise exc
        finally:
            context.close()
            browser.close()


if __name__ == "__main__":
    test_journey()
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

---

## Database Persistence & Tables

When the Test Builder node generates test scripts, they are persisted both to disk and indexed directly into the database:

1. **`forge.tests`** (Primary Test Repository):
   - **`test_id`**: Globally unique test identifier (e.g. `ws13_smoke_navigation_home`).
   - **`website_id`**: Associated website ID from `forge.websites`.
   - **`domain`** & **`page_url`**: Target site domain and target page URL.
   - **`title`**, **`description`**, **`category`**, **`priority`**: Human-readable metadata and scheduling priority.
   - **`script_path`**: Absolute path on disk to the generated `.py` script.
   - **`test_code`**: Complete source code of the Python test script.
   - **`language`**: Script runtime language (`python` or `typescript`).
   - **`cron_expression`**, **`cron_interval_hours`**, **`schedule_offset_seconds`**, **`next_run_at`**: Recurrence cadence and distributed target timestamps computed via `scheduler.spacing.compute_next_run`.

2. **`forge.test_runs`** (Execution Results):
   - Stores runtime results per execution (`run_id`, `test_id`, `status` [PASSED/FAILED], `exit_code`, `duration_s`, `stdout`, `stderr`, `screenshot_paths`, `trace_path`, `executed_at`).

3. **`forge.heals`** (Self-Healing Audit Trail):
   - Records any automated script fixes applied by the Self-Healing agent (`test_id`, `run_id`, `attempt`, `error_snippet`, `diagnosis`, `fix_plan`, `healed_at`).

