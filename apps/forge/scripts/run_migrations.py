import sys
from pathlib import Path

# Add src to sys.path
root_dir = Path(__file__).resolve().parent.parent
src_dir = root_dir / "src"
if str(src_dir) not in sys.path:
    sys.path.insert(0, str(src_dir))

from db.migrations import init_db
from db.connection import get_connection
from sqlalchemy import text
from alembic import command
from db.migrations import get_alembic_config


def main():
    print("=" * 60)
    print("RUNNING BESSEMER DATABASE MIGRATIONS (ALEMBIC)")
    print("=" * 60)
    
    # Run Alembic migrations
    init_db()
    print("\n[+] Alembic migrations successfully applied to 'head'.")

    # Display current tables and applied migrations
    with get_connection() as conn:
        tables = conn.execute(text(
            "SELECT table_name FROM information_schema.tables "
            "WHERE table_schema = 'forge' ORDER BY table_name;"
        )).fetchall()
        print(f"\nTables in 'forge' schema ({len(tables)}):")
        for t in tables:
            print(f"  - {t[0]}")

        version_rows = conn.execute(text(
            "SELECT version_num FROM forge.alembic_version;"
        )).fetchall()
        print(f"\nCurrent Alembic Version in forge schema:")
        for r in version_rows:
            print(f"  * version: {r[0]}")

    print("\n" + "=" * 60)
    print("MIGRATIONS COMPLETE")
    print("=" * 60)


if __name__ == "__main__":
    main()
