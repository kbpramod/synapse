"""Link pages to websites and tests to pages.

Revision ID: 0004
Revises: 0003
Create Date: 2026-09-08 18:35:00.000000

"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = '0004'
down_revision: Union[str, None] = '0003'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    conn = op.get_bind()

    # 1. Add website_id to pages table if not already present
    conn.execute(sa.text("""
        ALTER TABLE forge.pages ADD COLUMN IF NOT EXISTS website_id INTEGER;
    """))

    # Add foreign key constraint if not already present
    conn.execute(sa.text("""
        DO $$
        BEGIN
            IF NOT EXISTS (
                SELECT 1 FROM pg_constraint WHERE conname = 'fk_pages_website_id'
            ) THEN
                ALTER TABLE forge.pages
                ADD CONSTRAINT fk_pages_website_id
                FOREIGN KEY (website_id) REFERENCES forge.websites(id) ON DELETE CASCADE;
            END IF;
        END $$;
    """))

    # Add index if not already present
    conn.execute(sa.text("""
        CREATE INDEX IF NOT EXISTS idx_pages_website_id ON forge.pages (website_id);
    """))

    # 2. Add page_id to tests table if not already present
    conn.execute(sa.text("""
        ALTER TABLE forge.tests ADD COLUMN IF NOT EXISTS page_id INTEGER;
    """))

    # Add foreign key constraint if not already present
    conn.execute(sa.text("""
        DO $$
        BEGIN
            IF NOT EXISTS (
                SELECT 1 FROM pg_constraint WHERE conname = 'fk_tests_page_id'
            ) THEN
                ALTER TABLE forge.tests
                ADD CONSTRAINT fk_tests_page_id
                FOREIGN KEY (page_id) REFERENCES forge.pages(id) ON DELETE SET NULL;
            END IF;
        END $$;
    """))

    # Add index if not already present
    conn.execute(sa.text("""
        CREATE INDEX IF NOT EXISTS idx_tests_page_id ON forge.tests (page_id);
    """))

    # 3. Backfill website_id for existing pages matching website domain
    conn.execute(sa.text("""
        UPDATE forge.pages
        SET website_id = websites.id
        FROM forge.websites
        WHERE pages.domain = websites.domain
          AND pages.website_id IS NULL;
    """))

    # 4. Backfill page_id for existing tests matching page URL
    conn.execute(sa.text("""
        UPDATE forge.tests
        SET page_id = pages.id
        FROM forge.pages
        WHERE tests.page_url = pages.url
          AND tests.page_id IS NULL;
    """))


def downgrade() -> None:
    conn = op.get_bind()
    conn.execute(sa.text("""
        ALTER TABLE forge.tests DROP CONSTRAINT IF EXISTS fk_tests_page_id;
        DROP INDEX IF EXISTS forge.idx_tests_page_id;
        ALTER TABLE forge.tests DROP COLUMN IF EXISTS page_id;

        ALTER TABLE forge.pages DROP CONSTRAINT IF EXISTS fk_pages_website_id;
        DROP INDEX IF EXISTS forge.idx_pages_website_id;
        ALTER TABLE forge.pages DROP COLUMN IF EXISTS website_id;
    """))
