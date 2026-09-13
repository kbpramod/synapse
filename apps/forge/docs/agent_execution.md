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
- **`[FAILURE_URL]`**: The actual URL the browser is on when an exception or assertion occurs. Crucial for detecting silent HTTP redirects (e.g. redirecting from `/inventory.html` or `/dashboard` to `/` or `/login`).
- **`[VISIBLE_ERRORS]`**: Error banners, toasts, and alert headings (`.error`, `.alert`, `[role='alert']`, `[data-test='error']`, `h1-h3`) visible on the failure page (e.g. *"Epic sadface: You can only access '/inventory.html' when you are logged in"*).
- **Failure Screenshot**: Captured to `<test_name>_failure.png` before context termination.
- **`[FINAL_URL]`**: Emitted upon journey success to automatically trigger background onboarding for newly reached authenticated pages.

### 2. Defect Analysis (`analyzer`)
The Defect Analyzer classifies execution outcomes into three distinct categories:
- **`PASS`**: All functional state transitions and assertions succeeded.
- **`NEED_HEAL` (Test Automation Defect)**: The application is behaving normally, but the automation failed:
  - **Authentication Redirect**: The test navigated directly to a protected route without logging in or without loading session credentials.
  - **Locator / Responsive Variant**: The element is hidden inside a collapsed drawer, mobile menu, or changed selector.
  - **Timing & Waiting**: Dynamic DOM render timing or race conditions.
- **`SUSPECTED_APP_FAILURE` (Application Bug)**: True application defects (HTTP 500/502/503 errors, unhandled JavaScript exceptions, or broken application business logic).

### 3. Self-Healing & Session State Reuse (`healer` -> `editor`)
When a test fails due to an authentication redirect or missing credentials:
1. **Healer Diagnosis**: Combines `failure_url`, `redirect_detected`, `visible_errors_on_page`, and registered `available_accounts`.
2. **Session Identification**: Automatically detects existing `*.storage_state.json` session files in `storage/<domain>/tests/`.
3. **Tactical Fix Plan**: Formulates precise instructions for the `editor` node to either:
   - Load the existing `storage_state` in `browser.new_context(storage_state=...)`.
   - Prepend login steps utilizing registered user credentials before navigating to protected routes.
4. **Editor Execution**: The Editor patches the script in place, preserving existing journey steps while resolving the prerequisite defect.

### 4. Transparent Healer & Discovery Diagnostics
To eliminate "black box" behavior when tests fail or elements seem missing, the Healer emits structured, traceable logs:
- **`[HEAL:DISCOVERY]`**: Discloses the discovery snapshot source (`state`, disk cache `discovery.json`, or PostgreSQL), snapshot URL vs target URL, and raw element counts. Highlights any URL mismatch (e.g. if discovery was redirected to login).
- **`[HEAL:LOCATOR_MATCH]`**: Explicitly cross-checks the failed locator or text from `error_summary` / `stderr` against raw discovery buttons, inputs, and links to report whether the element was ever observed on that page.
- **`[HEAL:ELEMENTS_FEED]` & `[HEAL:ELEMENTS_FEED:OMITTED]`**: Details the exact number of buttons, inputs, links, and selects passed into the LLM prompt, prioritizes the targeted selector, and logs what elements were omitted to protect prompt context limits.
- **`[HEAL:TELEMETRY]`**: Summarizes target URL, failure landing URL, redirect flags, on-screen error banners, available `storage_state`, and registered accounts.
- **`[HEAL:LLM_RESULT]`**: Displays the final Failure Class, Root-Cause Diagnosis, Fix Plan, and Preserved test assertions.

### 5. Architectural Invariant: DOM Locators Are Immutable Technical Evidence
Locators discovered from the live DOM by the Discovery Agent represent immutable technical ground truth. Across all agent hand-offs (Discovery → Planner → Builder → Healer → Editor → Runner):

1. **Zero Spelling Normalization**:
   Agents must NEVER spell-correct, normalize, or semantically rewrite discovered attributes:
   - *Discovery Agent*: Finds locator `#susbscribe_email`
   - *Planner / Builder / Healer*: Must preserve `#susbscribe_email` verbatim. Never treat it as a typo or rewrite it to `#subscribe_email`.
   - *Playwright Runner*: Executes against the real DOM element without timeouts.

2. **Exact Selector Priority Over Guessed Semantic Names**:
   When an element in the DOM has an ID or unique selector (such as `<button id="subscribe">` containing an arrow icon and no text content), agents must prioritize `page.locator("#subscribe")`. Inventing semantic role selectors like `get_by_role("button", name=re.compile("subscribe"))` fails on icon-only or text-less controls.

3. **Relevance-Ranked DOM Element Feeds**:
   Instead of naively truncating elements with `[:15]` from the top of the DOM, the Builder, Healer, and Editor rank elements by relevance scoring against scenario keywords, error summaries, and element IDs. This guarantees off-screen or footer form elements (e.g. submit buttons located deep in the DOM) are never truncated or lost from agent context windows.

4. **Idempotent Healing Guard**:
   When an Editor modification produces byte-identical code (`NO CHANGE APPLIED`), the system flags the lack of diff to avoid burning healing iterations on idempotent failures.


---

## 5. Distributed Scheduled Execution

Scheduled tests are orchestrated via the Forge Scalable Distributed Scheduler:
1. **Periodic Claim**: Scheduler nodes periodically select due tests from PostgreSQL using `FOR UPDATE SKIP LOCKED`.
2. **De-Bunching & Window Spacing**: Recurrence offsets are distributed across frequency windows to eliminate spikes.
3. **Queueing & Concurrency**: Jobs are placed into Redis priority queues (`critical` > `high` > `medium` > `low`) and respect per-website concurrency limits.
4. **Worker Consumption**: Independent Playwright workers consume jobs via `BLPOP` and execute the test pipeline (`run_single_test`), recording execution results to `test_runs` and heals to `heals`.

See [scheduler.md](scheduler.md) for full architecture and endpoint specifications.
