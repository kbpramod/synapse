# Forge Autonomous Agent Execution

This document describes how to execute and customize the Forge Autonomous QA Agent loop.

## Overview

The Forge Agent is powered by LangGraph, implementing a 9-node reactive state graph:

1. **`discover`**: Navigates to the target page via Playwright, extracting interactive elements, DOM layout, and console/network telemetry.
2. **`understanding`**: Uses LLM semantic analysis to identify page type, purpose, and key functional user actions.
3. **`planner`**: Formulates prioritized test scenarios (smoke, primary flows, validation, edge cases).
4. **`builder`**: Generates resilient Playwright test scripts (defaults to Python `.py` scripts, with optional TypeScript `.spec.ts`) targeting discovered element selectors and `forge_id` markers.
5. **`runner`**: Executes the test script in an isolated subprocess honoring headed/headless settings (`sys.executable` for Python, `npx playwright` for TS).
6. **`observer`**: Gathers screenshots, traces, stderr, and failure artifacts.
7. **`analyzer`**: Categorizes run results (`PASS`, `NEED_HEAL`, `APP_BUG`, `ENV_ERROR`).
8. **`healer`**: Evaluates failure telemetry, updates locator strategies, and triggers re-generation.
9. **`advance_test`**: Cycles to the next planned scenario until all tests in the plan are completed.

---

## Running the Agent Loop

Execute the loop against any target URL:

```bash
uv run test-scripts/agent_loop.py https://wecatchai.com/
```

### CLI Arguments

| Argument | Type | Default | Description |
| :--- | :--- | :--- | :--- |
| `url` | Positional | `https://example.com` | Target web application URL. |
| `--headed` | Flag | `False` | Forces browser to launch visibly (`headless=False`). |
| `--headless` | String / Bool | Auto (from `.env`) | Explicitly set headless mode (`true` or `false`). |
| `--lang` / `--language` | Choice | `python` | Script language: `python` (default) or `typescript`. |
| `--timeout` | Integer | `40` | Playwright test timeout in seconds. |
| `--max-heals` | Integer | `2` | Maximum self-healing attempts per test scenario. |

### Examples

```bash
# Run against a target site with visible browser window generating Python scripts
uv run test-scripts/agent_loop.py https://wecatchai.com/ --headed

# Explicitly choose Python or TypeScript
uv run test-scripts/agent_loop.py https://wecatchai.com/ --lang python
uv run test-scripts/agent_loop.py https://wecatchai.com/ --lang typescript

# Run headless with custom timeout
uv run test-scripts/agent_loop.py https://wecatchai.com/ --headless true --timeout 60
```

---

## Telemetry, Defect Classification & Self-Healing

### 1. Failure Telemetry & Diagnostics
Generated test scripts are equipped with inline exception handlers that capture real-time application context at the exact moment of failure:
- **`[FAILURE_URL]`**: The actual URL the browser is on when an exception or assertion occurs. Crucial for detecting navigation destinations vs unexpected HTTP redirects (e.g. redirecting from protected routes to `/login`).
- **`[VISIBLE_ERRORS]`**: Explicitly targets true error containers (`[role='alert']`, `.alert-danger`, `.alert-warning`, `.error-message`, `.error`, `.invalid-feedback`, `[data-test='error']`). Normal page headings (`h1-h3`) and success notifications (`.alert-success`) are strictly excluded to avoid contaminating the diagnostic evidence pipeline.
- **`[ERROR_ELEMENTS]`**: Captures the exact CSS selector producing the error text.
- **Failure Screenshot**: Captured to `<test_name>_failure.png` before context termination.
- **`[FINAL_URL]`**: Emitted upon journey success to automatically trigger background onboarding for newly reached authenticated pages.

### 2. Single Source of Truth (`failure_context`) & Defect Analysis
To prevent downstream agents from independently re-interpreting raw telemetry, the `analyzer` normalizes failure evidence into a unified `FailureContext`:
```python
failure_context = {
    "target_url": "https://example.com/",
    "failure_url": "https://example.com/contact_us",
    "discovery_url": "https://example.com/contact_us",
    "navigation": {
        "expected": True,
        "auth_redirect": False,
    },
    "page_loaded": True,
    "visible_errors": [],
    "error_elements": [],
    "error_summary": "...",
}
```

The Analyzer classifies execution outcomes into three distinct categories:
- **`PASS`**: All functional state transitions and assertions succeeded.
- **`NEED_HEAL` (Test Automation Defect)**: The application is functioning normally, but the automation failed:
  - **Navigation vs Auth Disambiguation**: Forward journey navigation (e.g. `/` -> `/contact_us` or `/products`) is classified as expected application progression (`auth_redirect: False`). An authentication defect is diagnosed *only* when the application redirected to an unexpected authentication barrier (e.g. `/login` or `/users/sign_in`) or displayed an explicit authentication error banner.
  - **Locator / Responsive Variant**: The element is hidden inside a collapsed drawer, mobile menu, or changed selector.
  - **Timing & Waiting**: Dynamic DOM render timing or race conditions.
- **`SUSPECTED_APP_FAILURE` (Application Bug)**: Severe backend errors (HTTP 500/502/503), unhandled application JavaScript crashes, or maximum heal budget reached.

### 3. Destination-Scoped Discovery & Self-Healing Pipeline
The Cron Graph executes discovery *before* diagnosis to ensure the Healer operates on ground-truth destination DOM evidence:
```text
runner -> observer -> analyzer -> discover_for_heal -> healer -> editor -> route_editor
```
1. **Destination Scoping**: Discovery targets `discovery_url` (`failure_url` if valid HTTP/HTTPS, else `target_url`). If a test navigated from `/` and failed on `/contact_us`, discovery inspects `/contact_us`, capturing its live headings, inputs, and buttons.
2. **Healer Diagnosis**: Inspects the destination DOM elements and headings. If the test failed asserting `h1: "Contact Us"`, the Healer observes destination headings (`h2: "GET IN TOUCH"`, `h2: "FEEDBACK FOR US"`) and plans a grounded locator fix rather than hallucinating an authentication issue.
3. **Editor Circuit Breaker (`edit_status`)**:
   Tracks explicit modification status (`applied`, `no_change`, `failed`):
   - `applied`: Script successfully updated and validated via AST parsing -> routes to `runner` for re-execution.
   - `no_change` / `failed`: Circuit breaker halts retries immediately, records the failure in PostgreSQL, advances the schedule, and routes to `get_next_test`. Wasted retries on identical code are eliminated.

### 4. Evidence-Based Deterministic Verifier
When max heals are exceeded or an unresolvable defect occurs, the Verifier classifies evidence deterministically without spawning fragile secondary LLM smoke tests:
- **`CONFIRMED_APP_BUG`**: Server returned 5xx, connection refused, or unhandled crash -> Incident report filed.
- **`FAILED_AUTOMATION`**: Auth barrier reached, or destination DOM loaded with existing page headings/buttons -> Script/assertion defect.
- **`INCONCLUSIVE`**: Ambiguous evidence -> Terminated cleanly, recorded in PostgreSQL, schedule advanced, advances to `get_next_test`. Under no circumstances does an inconclusive verdict re-loop or spin up secondary broken probes.

### 5. Architectural Invariants
1. **Zero Spelling Normalization**: Locators discovered from the DOM are exact technical evidence. Never spell-correct or normalize discovered attributes (`#susbscribe_email` remains `#susbscribe_email`).
2. **Exact Selector Priority**: Buttons with unique IDs (`#subscribe`) take precedence over invented semantic role locators.
3. **Relevance-Ranked DOM Elements**: Elements are scored by keyword and error summary relevance rather than arbitrary top-of-DOM truncation.


---

## 5. Distributed Scheduled Execution

Scheduled tests are orchestrated via the Forge Scalable Distributed Scheduler:
1. **Periodic Claim**: Scheduler nodes periodically select due tests from PostgreSQL using `FOR UPDATE SKIP LOCKED`.
2. **De-Bunching & Window Spacing**: Recurrence offsets are distributed across frequency windows to eliminate spikes.
3. **Queueing & Concurrency**: Jobs are placed into Redis priority queues (`critical` > `high` > `medium` > `low`) and respect per-website concurrency limits.
4. **Worker Consumption**: Independent Playwright workers consume jobs via `BLPOP` and execute the test pipeline (`run_single_test`), recording execution results to `test_runs` and heals to `heals`.

See [scheduler.md](scheduler.md) for full architecture and endpoint specifications.
