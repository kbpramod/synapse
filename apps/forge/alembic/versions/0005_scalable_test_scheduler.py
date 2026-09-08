"""Add scalable scheduler fields: enabled, timezone, offset to tests, concurrency_limit to websites.

Revision ID: 0005
Revises: 0004
Create Date: 2026-09-08 20:15:00.000000

"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = '0005'
down_revision: Union[str, None] = '0004'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    conn = op.get_bind()

    # 1. Add scheduler columns to forge.tests
    conn.execute(sa.text("""
        ALTER TABLE forge.tests ADD COLUMN IF NOT EXISTS enabled BOOLEAN DEFAULT TRUE NOT NULL;
    """))

    conn.execute(sa.text("""
        ALTER TABLE forge.tests ADD COLUMN IF NOT EXISTS timezone VARCHAR(50) DEFAULT 'UTC' NOT NULL;
    """))

    conn.execute(sa.text("""
        ALTER TABLE forge.tests ADD COLUMN IF NOT EXISTS schedule_offset_seconds INTEGER DEFAULT 0 NOT NULL;
    """))

    # Add partial index for rapid due test scans
    conn.execute(sa.text("""
        CREATE INDEX IF NOT EXISTS idx_tests_due_lookup ON forge.tests (enabled, next_run_at) WHERE enabled = TRUE;
    """))

    # 2. Add concurrency_limit to forge.websites
    conn.execute(sa.text("""
        ALTER TABLE forge.websites ADD COLUMN IF NOT EXISTS concurrency_limit INTEGER DEFAULT 2 NOT NULL;
    """))


def downgrade() -> None:
    conn = op.get_bind()
    conn.execute(sa.text("""
        DROP INDEX IF EXISTS forge.idx_tests_due_lookup;
        ALTER TABLE forge.tests DROP COLUMN IF EXISTS schedule_offset_seconds;
        ALTER TABLE forge.tests DROP COLUMN IF EXISTS timezone;
        ALTER TABLE forge.tests DROP COLUMN IF EXISTS enabled;
        ALTER TABLE forge.websites DROP COLUMN IF EXISTS concurrency_limit;
    """))
