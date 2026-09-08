# Forge Configuration Guide

This document outlines the configuration options for the Forge Autonomous Testing Agent and Pipeline.

## Environment Variables (.env)

Configuration is loaded from the root `.env` file or from environment variables.

| Variable | Default | Description |
| :--- | :--- | :--- |
| `FORGE_HEADLESS` / `HEADLESS` | `true` | Controls browser execution mode. Set to `false`, `0`, `no`, or `headed` to run visible/headed browsers. |
| `FORGE_STORAGE_ROOT` | `<project_root>/storage` | Root directory where generated test scripts, screenshots, and page discovery files are persisted. |
| `DATABASE_URL` | `""` | Neon PostgreSQL database connection string (schema: `forge`). |
| `AICREDITS_API_KEY` | `""` | API key for LLM orchestration (Page Understanding, Planning, Code Generation, Healing). |
| `AICREDITS_BASE_URL` | `https://api.aicredits.in/v1` | Base URL for LLM chat completion endpoint. |
| `AICREDITS_MODEL` | `gpt-4o-mini` | Default chat model used by the agents. |
| `FORGE_DISCOVERY_TIMEOUT_MS` | `30000` | Discovery timeout in milliseconds (default: 30s). |
| `FORGE_TEST_TIMEOUT_S` | `45` | Default test script execution timeout in seconds (default: 45s). |
| `FORGE_TEST_LANGUAGE` | `python` | Test script language (`python` or `typescript`). Defaults to Python (`.py`) for native execution. |

---

## Headless vs. Headed Mode

Forge supports both headless and visible (headed) browser execution across all pipeline stages:

### 1. Centralized Configuration via `.env`
Set `FORGE_HEADLESS=false` in `.env`:
```ini
FORGE_HEADLESS=false
```
When `FORGE_HEADLESS=false` is set:
- Single-page and BFS site discovery launch visible Chromium instances.
- StateGraph runner node passes `--headed` to `npx playwright test` and sets `HEADLESS=false` for subprocess execution.
- Cron regression and stage runners default to visible browsers.

### 2. Command-Line Overrides
You can also override headless mode dynamically via CLI flags:

#### Agent Loop (`test-scripts/agent_loop.py`):
```bash
# Explicitly launch in headed mode (visible browser window)
uv run test-scripts/agent_loop.py https://example.com/ --headed

# Explicitly launch in headless mode
uv run test-scripts/agent_loop.py https://example.com/ --headless true
```

#### Individual Stages (`scripts/run_stage.py`):
```bash
# Run discovery stage in headed mode
uv run scripts/run_stage.py --stage discover --url https://example.com/ --headed

# Run test execution stage in headed mode
uv run scripts/run_stage.py --stage run --url https://example.com/ --headed
```

#### Hourly Regression Runner (`scripts/run_cron.py`):
```bash
uv run scripts/run_cron.py --headed
```

---

## Database Schema & Migrations (`forge` Schema)

Forge isolates all its persistence models and migration tracking inside a dedicated PostgreSQL schema (`forge`) rather than the default `public` or `dbo` schemas.

### Schema Architecture
- **Isolated Tables**:
  - `forge.websites`: Discovered websites and domain metadata
  - `forge.accounts`: Authentication credentials and role-based test users
  - `forge.pages`: Crawled page maps, URLs, preconditions, and primary actions
  - `forge.elements`: Identified interactive DOM elements and selectors
  - `forge.tests`: Planned and generated test scripts, priorities, and cron schedules
  - `forge.test_runs`: Execution logs, exit codes, screenshots, and trace paths
  - `forge.heals`: Self-healing audit trails, error diagnosis, and code patches
  - `forge.alembic_version`: Alembic migration revision pointer (does not collide with `public.alembic_version`)

### Connection Search Path
Connections yielded by `db.connection.get_connection()` automatically configure:
```sql
CREATE SCHEMA IF NOT EXISTS forge;
SET search_path TO forge, public;
```
All SQLAlchemy declarative models in `src/models/base.py` define `metadata = MetaData(schema="forge")`.

### Running Migrations
To apply pending database migrations to the `forge` schema:
```bash
uv run python scripts/run_migrations.py
```
Or check table status and columns across schemas:
```bash
uv run python scripts/check_tables.py
```
