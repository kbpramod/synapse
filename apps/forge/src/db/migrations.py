import logging
from sqlalchemy import text
from db.connection import get_connection

logger = logging.getLogger("forge.db.migrations")

INIT_SQL = """
-- 1. Create dedicated forge schema for 100% isolation from existing tables
CREATE SCHEMA IF NOT EXISTS forge;

-- 2. Websites catalog
CREATE TABLE IF NOT EXISTS forge.websites (
    id SERIAL PRIMARY KEY,
    domain VARCHAR(255) NOT NULL UNIQUE,
    start_url TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    last_discovered_at TIMESTAMPTZ
);

-- 3. Discovered Pages
CREATE TABLE IF NOT EXISTS forge.pages (
    id SERIAL PRIMARY KEY,
    domain VARCHAR(255) NOT NULL,
    url TEXT NOT NULL UNIQUE,
    title TEXT,
    slug VARCHAR(255),
    page_type VARCHAR(100),
    purpose TEXT,
    primary_actions JSONB,
    state_preconditions TEXT,
    discovered_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- 4. Discovered Interactive Elements (with deterministic forge_id)
CREATE TABLE IF NOT EXISTS forge.elements (
    id SERIAL PRIMARY KEY,
    forge_id VARCHAR(100) NOT NULL,
    page_url TEXT NOT NULL,
    tag VARCHAR(50) NOT NULL,
    element_type VARCHAR(50),
    text TEXT,
    selector TEXT NOT NULL,
    bounding_box JSONB,
    discovered_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT uq_forge_element UNIQUE (forge_id, page_url)
);

-- 5. Planned & Generated Tests
CREATE TABLE IF NOT EXISTS forge.tests (
    id SERIAL PRIMARY KEY,
    test_id VARCHAR(100) NOT NULL UNIQUE,
    domain VARCHAR(255) NOT NULL,
    page_url TEXT,
    title TEXT NOT NULL,
    description TEXT,
    category VARCHAR(50) DEFAULT 'regression',
    priority VARCHAR(20) DEFAULT 'medium',
    steps JSONB,
    expected_outcome TEXT,
    script_path TEXT,
    test_code TEXT,
    language VARCHAR(20) DEFAULT 'typescript',
    status VARCHAR(50) NOT NULL DEFAULT 'active',
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- 6. Test Run Executions (For Cron & On-Demand Runs)
CREATE TABLE IF NOT EXISTS forge.test_runs (
    id SERIAL PRIMARY KEY,
    run_id VARCHAR(100) NOT NULL,
    test_id VARCHAR(100) NOT NULL,
    exit_code INT NOT NULL,
    status VARCHAR(50) NOT NULL, -- 'PASSED', 'FAILED', 'HEALED'
    duration_s FLOAT DEFAULT 0.0,
    error_summary TEXT,
    stdout TEXT,
    stderr TEXT,
    screenshot_paths JSONB,
    trace_path TEXT,
    executed_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- 7. Self-Healing Audit Trail
CREATE TABLE IF NOT EXISTS forge.heals (
    id SERIAL PRIMARY KEY,
    test_id VARCHAR(100) NOT NULL,
    run_id VARCHAR(100),
    attempt INT NOT NULL DEFAULT 1,
    error_snippet TEXT,
    diagnosis TEXT NOT NULL,
    fix_plan TEXT NOT NULL,
    healed_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- Indexes for lightning fast lookups & cron querying
CREATE INDEX IF NOT EXISTS idx_forge_pages_domain ON forge.pages (domain);
CREATE INDEX IF NOT EXISTS idx_forge_elements_forge_id ON forge.elements (forge_id);
CREATE INDEX IF NOT EXISTS idx_forge_tests_domain ON forge.tests (domain);
CREATE INDEX IF NOT EXISTS idx_forge_tests_status ON forge.tests (status);
CREATE INDEX IF NOT EXISTS idx_forge_runs_test_id ON forge.test_runs (test_id);
CREATE INDEX IF NOT EXISTS idx_forge_runs_executed_at ON forge.test_runs (executed_at DESC);
"""


def init_db() -> None:
    """
    Initializes the isolated `forge` schema and tables in Neon PostgreSQL.
    Completely non-destructive: uses IF NOT EXISTS and never drops or alters existing tables.
    """
    logger.info("Initializing isolated 'forge' schema in PostgreSQL...")
    with get_connection() as conn:
        conn.execute(text(INIT_SQL))
    logger.info("Database schema 'forge' and tables verified successfully.")
