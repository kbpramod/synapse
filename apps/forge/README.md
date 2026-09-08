# Backend Service

Backend service for Bessemer built with FastAPI and Python `>=3.11`, managed by [`uv`](https://docs.astral.sh/uv/).

## Quick Start

### 1. Prerequisites
- Python `>= 3.11`
- `uv >= 0.12.0`

### 2. Setup Environment
```bash
# Sync dependencies and create .venv
uv sync
```

### 3. Run Backend
```bash
# Run entrypoint script
uv run backend

# Or run FastAPI development server
uv run uvicorn backend:app --reload --port 8000
```

For full setup and contribution guidelines, see [CONTRIBUTING.md](../CONTRIBUTING.md).
