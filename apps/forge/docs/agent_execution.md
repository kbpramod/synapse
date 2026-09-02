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
