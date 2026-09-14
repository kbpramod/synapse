# Python Playwright Test Scripts in Forge

Forge generates native Python test scripts (`.py`) by default using the Playwright Python API (`playwright.sync_api`).

## Why Python Test Scripts?

1. **Zero External Runtime Dependencies**: Tests run directly inside the Python virtual environment (`.venv`) using `sys.executable`, eliminating the need for `npx`, `node_modules`, or TypeScript compilation overhead.
2. **Fast, Predictable Execution**: Direct execution via Python subprocess ensures consistent execution across operating systems (Windows, Linux, macOS).
3. **Rich Debugging and Stack Traces**: Python assertion errors (e.g. from `expect(locator).to_be_visible()`) produce tracebacks that are directly parsed by the Self-Healing and Defect Analyzer nodes.
4. **Pytest and Direct Script Compatible**: Scripts can be run directly via `python <test>.py` or through `pytest`.

---

## Script Architecture & Provenance

In the decoupled architecture, Python test scripts are not written monolithically. Instead, they are assembled by `testcase_assembler_node` from:
1. **Validated `ActionSpec`**: Pure Playwright interactions verified on real DOM with zero speculative assertions.
2. **Verified `ExpectationSpec`**: Assertions synthesized from post-action telemetry (DOM delta, navigation, network status) and strictly grounded in the Journey Intent.
3. **`TestProvenance`**: Embedded machine-readable JSON metadata capturing timestamps, validation status, confidence scores, and healing attempts.

Each generated Python test script follows this resilient structure:

```python
"""
================================================================================
FORGE DECOUPLED TEST CASE: ws13_smoke_navigation_home
================================================================================
Generated with strict Action/Expectation separation and Provenance Tracking.

PROVENANCE:
{
  "test_id": "ws13_smoke_navigation_home",
  "generated_at": "2026-09-13T19:24:20Z",
  "action_provenance": {
    "validated": true,
    "steps_count": 3,
    "heals_needed": 0,
    "validated_at": "2026-09-13T19:24:10Z"
  },
  "expectation_provenance": [
    {
      "assertion": "expect(page.locator('h1:has-text(\"Example Domains\")')).to_be_visible()",
      "type": "visible_element",
      "confidence": 0.95,
      "evidence": ["DOM contains heading 'Example Domains'"],
      "heals_needed": 0
    }
  ]
}
================================================================================
"""
import os
import re
import sys
from playwright.sync_api import sync_playwright, expect


def test_journey():
    headless = os.getenv("HEADLESS", "true").lower() in ("true", "1", "yes")
    
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=headless)
        context = browser.new_context(viewport={"width": 1280, "height": 800})
        page = context.new_page()
        
        try:
            # === SECTION 1: VERIFIED ACTIONS ===
            page.goto("https://example.com", wait_until="domcontentloaded", timeout=30000)
            page.locator("a:has-text('Learn more')").click()
            
            # === SECTION 2: GROUNDED EXPECTATIONS ===
            assert page.url != "https://example.com"
            expect(page.locator('h1:has-text("Example Domains")')).to_be_visible()
            
            # Telemetry markers on success
            print(f"[FINAL_URL] {page.url}")
            print("[TEST PASSED] ws13_smoke_navigation_home")
            
        finally:
            context.close()
            browser.close()


if __name__ == "__main__":
    test_journey()
```

### Clean Architecture Principles: Zero Clutter & Zero Swallowed Exceptions
1. **Four Core Sections Only**: Setup, User Actions, Grounded Assertions, and Cleanup.
2. **No Embedded Diagnostic Blocks**: Generated test scripts do NOT contain `except Exception as exc:` blocks. Telemetry and failure-evidence collection (failure screenshot, failure URL, and visible error elements) are handled externally by `runner.harness`.
3. **No Swallowed Exceptions**: Code does not swallow errors with arbitrary `try/except: pass` blocks. Playwright assertion auto-waiting (`expect(...)`) and native timeouts manage timing and state transitions reliably.
4. **Automatic Session Storage Persistence**: Successful sessions are persisted automatically by the runner harness upon context closure without cluttering test code.

---

## Semantic Alert & Error Handling

Web applications frequently use generic alert styling containers (e.g. Bootstrap `.alert`) for success messages, informational notes, and warnings. The Forge expectation engine and test assertions enforce strict semantic distinctions:

```text
.alert (Generic UI container — NOT an error!)
├── .alert-success     → Success confirmation (assert on successful flows)
├── .alert-danger      → Error message (assert on negative flows)
├── .alert-info        → Informational notification
└── .alert-warning     → Warning notice
```

- **"No Error Message" Invariant**: When verifying that no error occurred, tests must NEVER assert `locator('.alert').to_have_count(0)` or `locator('[role=alert]').to_have_count(0)`, as doing so triggers false failures on success confirmation banners (e.g. `.alert-success`).
- **Target Explicit Error Selectors**: Assertions strictly target explicit error classes: `.alert-danger, .error-message, .error-msg, .invalid-feedback, [data-test='error'], [aria-invalid='true'], .text-danger`.

---

## Runner Harness & Externalized Evidence Collection

Tests executed by the platform are invoked through `runner.harness` (`python -m runner.harness <test_file.py>`):
- **On Success**: Automatically saves authenticated browser state (`<test_stem>.storage_state.json`) and reports completion metrics.
- **On Failure**: Transparently intercepts active exception unwinding before context closure:
  - Captures `[FAILURE_URL] <url>`
  - Takes failure screenshot (`<test_stem>_failure.png`)
  - Evaluates semantic error elements (ignoring false-positive success banners) and emits `[VISIBLE_ERRORS]`
  - Re-raises the original exception with complete Playwright stack trace and non-zero exit code.

---

## Self-Healing Active Test Code Contract

When a test failure occurs and routes to `healer_node`:
- **Single Source of Truth**: Healer reads the active `test_file_path` directly from disk (the exact script executed by the Runner in this iteration).
- **Strict Version Tracking**: Healer is guaranteed to receive `test.py vN` for iteration `N`, preventing reasoning over stale or pre-edit script versions across consecutive healing attempts.

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

```bash
# Option 1: Run the test script directly with python (headed by default)
uv run python playwright-tests/test_001.py

# Option 2: Use the standalone test runner script
uv run python playwright-tests/run_test.py playwright-tests/test_001.py

# Option 3: Run headless via the runner or env flag
uv run python playwright-tests/run_test.py playwright-tests/test_001.py --headless

# Option 4: Execute with pytest
uv run pytest playwright-tests/test_001.py -s
```

---

## Supabase Test Artifact Directory Architecture

Rather than treating a single `.py` test file as the sole source of truth, Forge manages each test as a structured **Test Artifact Package** backed durably by **Supabase Storage**.

The structured specifications (`action.json`, `expectations.json`, `verification.json`) are the immutable sources of truth. `action.py` and `test.py` are derived executable artifacts generated from them.

### Storage Layout (`<domain>/tests/<test_id>/`)

```text
tests/
└── ws13_flow_contact_us/
    │
    ├── manifest.json            # Entry point: version, status, validated flags, artifacts map, last_run
    │
    ├── action.json              # Structured ActionSpec (pure interactions, locators, inputs, steps)
    ├── action.py                # Derived Playwright execution script for the action sequence
    │
    ├── action_result.json       # Mechanical execution telemetry (passed, exit_code, duration_s, failure_url)
    │
    ├── discovery_before.json    # Pre-action DOM and page snapshot
    ├── discovery_after.json     # Post-action DOM delta, navigation transition, network status
    │
    ├── expectations.json        # Structured ExpectationSpec (grounded assertions, confidence, evidence)
    │
    ├── verification.json        # Machine-readable correctness verdict (verdict, confidence, evidence, reason)
    │
    ├── summary.json             # Human-readable and system metadata overview
    │
    └── test.py                  # Derived executable production Playwright test script
```

### Manifest Specification (`manifest.json`)

The manifest serves as the lightweight index for Cron runners and scheduler workers:

```json
{
  "test_id": "ws13_flow_contact_us",
  "version": 1,
  "status": "healthy",
  "domain": "example.com",
  "target_url": "https://example.com/contact",
  "storage_prefix": "example.com/tests/ws13_flow_contact_us",
  "artifacts": {
    "manifest": "manifest.json",
    "action": "action.json",
    "action_script": "action.py",
    "action_result": "action_result.json",
    "discovery_before": "discovery_before.json",
    "discovery_after": "discovery_after.json",
    "expectations": "expectations.json",
    "verification": "verification.json",
    "summary": "summary.json",
    "test": "test.py"
  },
  "validated": {
    "action": true,
    "expectations": true
  },
  "last_run": {
    "status": "passed",
    "verdict": "CORRECT",
    "timestamp": "2026-09-13T19:35:00Z",
    "duration_s": 2.14
  }
}
```

### Cron Execution & Targeted Failure Resolution

During scheduled Cron execution:
1. **Normal Run**: The worker downloads `test.py` (cached locally) and executes it directly with Playwright.
2. **On Failure**: Instead of feeding a monolithic 500-line script to an LLM to guess what went wrong, the worker inspects `manifest.json` and parses execution telemetry via `classify_test_failure`:
   - **Action Problem** (`TimeoutError`, locator mismatch, click failure before assertions):
     - Loads `action.json` + `discovery_before.json`.
     - Sends *only* action context to the Action Healer (`heal_action`). Assertions remain untouched.
   - **Expectation Problem** (`AssertionError`, `expect(...)` failure, text mismatch):
     - Loads `expectations.json` + `discovery_after.json`.
     - Sends *only* expectation context to the Expectation Healer (`heal_expectation`). Action steps remain untouched.
   - **Application Bug** (HTTP 5xx, crash banner):
     - Flags as genuine application incident without burning healing attempts.
3. **Regeneration**: After healing, `test.py` is regenerated from the updated specifications, `manifest.json` is updated with an incremented version, and all artifacts are synchronized back to Supabase Storage.


