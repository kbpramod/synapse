import os
from sqlalchemy import create_engine, text
from dotenv import load_dotenv

load_dotenv()
db_url = os.getenv("DATABASE_URL")
if db_url and db_url.startswith("postgres://"):
    db_url = db_url.replace("postgres://", "postgresql://", 1)

engine = create_engine(db_url)

with engine.begin() as conn:
    print("Beginning schema update transaction...")

    # 1. Update github_installations
    print("Updating github_installations columns...")
    conn.execute(text("ALTER TABLE github_installations ADD COLUMN IF NOT EXISTS organization_id VARCHAR;"))
    conn.execute(text("ALTER TABLE github_installations ADD COLUMN IF NOT EXISTS github_installation_id VARCHAR;"))
    conn.execute(text("ALTER TABLE github_installations ADD COLUMN IF NOT EXISTS github_account_id VARCHAR;"))
    conn.execute(text("ALTER TABLE github_installations ADD COLUMN IF NOT EXISTS github_account_login VARCHAR;"))
    conn.execute(text("ALTER TABLE github_installations ADD COLUMN IF NOT EXISTS github_account_type VARCHAR;"))
    conn.execute(text("ALTER TABLE github_installations ADD COLUMN IF NOT EXISTS installed_at TIMESTAMP;"))
    conn.execute(text("ALTER TABLE github_installations ADD COLUMN IF NOT EXISTS uninstalled_at TIMESTAMP;"))
    conn.execute(text("ALTER TABLE github_installations ADD COLUMN IF NOT EXISTS status VARCHAR NOT NULL DEFAULT 'active';"))

    # Copy data if old columns exist
    try:
        conn.execute(text("UPDATE github_installations SET github_installation_id = installation_id WHERE github_installation_id IS NULL AND installation_id IS NOT NULL;"))
        conn.execute(text("UPDATE github_installations SET github_account_id = account_id WHERE github_account_id IS NULL AND account_id IS NOT NULL;"))
        conn.execute(text("UPDATE github_installations SET github_account_login = account_login WHERE github_account_login IS NULL AND account_login IS NOT NULL;"))
        conn.execute(text("UPDATE github_installations SET github_account_type = account_type WHERE github_account_type IS NULL AND account_type IS NOT NULL;"))
        conn.execute(text("UPDATE github_installations SET installed_at = created_at WHERE installed_at IS NULL;"))
    except Exception as e:
        print("Data copy note:", e)

    # Clean old constraints / columns
    conn.execute(text("ALTER TABLE github_installations ALTER COLUMN user_id DROP NOT NULL;"))
    conn.execute(text("DROP INDEX IF EXISTS ix_github_installations_installation_id;"))
    conn.execute(text("ALTER TABLE github_installations DROP COLUMN IF EXISTS installation_id;"))
    conn.execute(text("ALTER TABLE github_installations DROP COLUMN IF EXISTS account_id;"))
    conn.execute(text("ALTER TABLE github_installations DROP COLUMN IF EXISTS account_login;"))
    conn.execute(text("ALTER TABLE github_installations DROP COLUMN IF EXISTS account_type;"))

    conn.execute(text("CREATE UNIQUE INDEX IF NOT EXISTS ix_github_installations_github_installation_id ON github_installations (github_installation_id);"))
    conn.execute(text("CREATE INDEX IF NOT EXISTS ix_github_installations_organization_id ON github_installations (organization_id);"))

    # Add FK to organizations
    try:
        conn.execute(text("ALTER TABLE github_installations DROP CONSTRAINT IF EXISTS fk_github_installations_organization_id;"))
        conn.execute(text("ALTER TABLE github_installations ADD CONSTRAINT fk_github_installations_organization_id FOREIGN KEY (organization_id) REFERENCES organizations(id) ON DELETE CASCADE;"))
    except Exception as e:
        print("FK org note:", e)

    # 2. Migrate data from repositories to github_repositories
    print("Migrating repositories to github_repositories...")
    conn.execute(text("""
        INSERT INTO github_repositories (
            id, installation_id, github_repository_id, name, full_name, private, active, user_id, owner,
            status, last_sync, knowledge_nodes_count, doc_pages_count, github_url, connected_at, created_at, updated_at
        )
        SELECT 
            r.id, r.github_installation_id, r.github_repository_id, r.name, r.full_name, false, true, r.user_id, r.owner,
            r.status, r.last_sync, r.knowledge_nodes_count, r.doc_pages_count, r.github_url, r.connected_at, r.created_at, r.updated_at
        FROM repositories r
        ON CONFLICT (id) DO NOTHING;
    """))

    # 3. Update doc_pages FK constraint
    print("Updating doc_pages FK...")
    conn.execute(text("ALTER TABLE doc_pages DROP CONSTRAINT IF EXISTS doc_pages_repository_id_fkey;"))
    conn.execute(text("ALTER TABLE doc_pages ADD CONSTRAINT doc_pages_repository_id_fkey FOREIGN KEY (repository_id) REFERENCES github_repositories(id) ON DELETE CASCADE;"))

    # Drop old repositories table
    print("Dropping old repositories table...")
    conn.execute(text("DROP TABLE IF EXISTS repositories CASCADE;"))

    # 4. Update alembic_version
    print("Updating alembic_version...")
    conn.execute(text("UPDATE alembic_version SET version_num = 'fbd566a32ac2';"))

print("Schema update completed successfully!")
