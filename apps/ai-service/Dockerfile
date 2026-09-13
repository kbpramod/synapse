FROM python:3.11-slim

# Copy uv and uvx binaries from official Astral uv image
COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/

WORKDIR /app

# Prevent bytecode generation, unbuffer logs, and compile bytecode during install
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    UV_COMPILE_BYTECODE=1

# 1. Copy dependency definitions
COPY pyproject.toml uv.lock ./

# 2. Install dependencies only (cached layer, skips installing root package)
RUN uv sync --frozen --no-dev --no-install-project

# 3. Copy application files and migrations
COPY README.md ./
COPY alembic.ini ./
COPY alembic ./alembic
COPY src ./src

# 4. Sync root project into the virtualenv
RUN uv sync --frozen --no-dev

EXPOSE 8000

ENV PATH="/app/.venv/bin:$PATH"
# Include both /app and /app/src to support direct and package-prefixed imports
ENV PYTHONPATH="/app:/app/src"

CMD ["uvicorn", "src.main:app", "--host", "0.0.0.0", "--port", "8000"]
