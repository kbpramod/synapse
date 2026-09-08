import sys
import json
from pathlib import Path

# Add src to sys.path
root_dir = Path(__file__).resolve().parent.parent
src_dir = root_dir / "src"
if str(src_dir) not in sys.path:
    sys.path.insert(0, str(src_dir))

from sqlalchemy import text
from sqlalchemy.orm import Session
from db.connection import get_engine, get_connection
from db.migrations import init_db
from db.repository import ForgeRepository
from models import Website, Account


def test_migration_and_tables():
    print("[TEST 1] Running database migrations from backend/migrations/...")
    init_db()

    with get_connection() as conn:
        rows = conn.execute(text(
            "SELECT table_name FROM information_schema.tables WHERE table_schema = 'forge' ORDER BY table_name;"
        )).fetchall()
        table_names = [r[0] for r in rows]
        print(f"  [PASS] Tables in forge schema: {table_names}")
        assert "alembic_version" in table_names, "Table 'alembic_version' was not created"
        assert "websites" in table_names, "Table 'websites' was not created"
        assert "accounts" in table_names, "Table 'accounts' was not created"
        assert "pages" in table_names, "Table 'pages' was not created"
        assert "elements" in table_names, "Table 'elements' was not created"
        assert "tests" in table_names, "Table 'tests' was not created"
        assert "test_runs" in table_names, "Table 'test_runs' was not created"
        assert "heals" in table_names, "Table 'heals' was not created"


def test_repository_crud():
    print("[TEST 2] Testing Repository CRUD for Websites and Accounts...")
    test_url = "https://db-test-sample.com"

    # Clean up previous run if any
    existing = ForgeRepository.get_website_by_url(test_url)
    if existing:
        ForgeRepository.delete_website(existing["id"])

    # 1. Create Website
    website = ForgeRepository.create_website(url=test_url, is_active=True)
    assert website["id"] is not None, "Website ID is None"
    assert website["url"] == test_url, "Website URL mismatch"
    assert website["is_active"] is True, "Website should be active"
    website_id = website["id"]
    print(f"  [PASS] Created website ID: {website_id} for {test_url}")

    # 2. Create Accounts for Website
    admin_acc = ForgeRepository.create_account(
        website_id=website_id,
        username="admin@sample.com",
        password="secure_admin_password_123",
        role="admin",
        credentials={"api_key": "ak_live_admin_999", "mfa_enabled": True},
        is_active=True,
    )
    assert admin_acc["id"] is not None
    assert admin_acc["role"] == "admin"
    assert admin_acc["credentials"]["api_key"] == "ak_live_admin_999"

    user_acc = ForgeRepository.create_account(
        website_id=website_id,
        username="tester@sample.com",
        password="user_password_456",
        role="user",
        credentials={"auth_token": "token_abc_123"},
        is_active=True,
    )
    assert user_acc["id"] is not None
    assert user_acc["role"] == "user"
    print(f"  [PASS] Created 2 accounts under website {website_id} (admin & user)")

    # 3. Query Accounts for Website
    accounts = ForgeRepository.list_accounts_for_website(website_id)
    assert len(accounts) == 2, f"Expected 2 accounts, got {len(accounts)}"

    # 4. Filter by Role
    admin_list = ForgeRepository.list_accounts_for_website(website_id, role="admin")
    assert len(admin_list) == 1
    assert admin_list[0]["username"] == "admin@sample.com"
    print("  [PASS] Successfully queried and filtered accounts by role.")

    # 5. Test SQLAlchemy ORM Models & Relationship
    engine = get_engine()
    with Session(engine) as session:
        orm_website = session.query(Website).filter_by(id=website_id).first()
        assert orm_website is not None
        assert len(orm_website.accounts) == 2, f"Expected 2 accounts via ORM relationship, got {len(orm_website.accounts)}"
        print(f"  [PASS] ORM relationship verified: Website '{orm_website.url}' has {len(orm_website.accounts)} accounts.")

    # 6. Test Cascade Deletion
    ForgeRepository.delete_website(website_id)
    remaining_accounts = ForgeRepository.list_accounts_for_website(website_id)
    assert len(remaining_accounts) == 0, "Accounts were not cascade-deleted with website!"
    print("  [PASS] Cascade deletion verified: Deleting website removed all associated accounts.")


if __name__ == "__main__":
    print("=" * 60)
    print("RUNNING WEBSITES & ACCOUNTS DATABASE VERIFICATION")
    print("=" * 60)
    test_migration_and_tables()
    test_repository_crud()
    print("=" * 60)
    print("ALL DATABASE TESTS PASSED SUCCESSFULLY!")
    print("=" * 60)
