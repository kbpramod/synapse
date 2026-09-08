import sys
from pathlib import Path

root_dir = Path(__file__).resolve().parent.parent
src_dir = root_dir / "src"
if str(src_dir) not in sys.path:
    sys.path.insert(0, str(src_dir))

from db.connection import get_connection
from sqlalchemy import text

with get_connection() as conn:
    print("SCHEMAS:")
    schemas = conn.execute(text("SELECT schema_name FROM information_schema.schemata;")).fetchall()
    print([s[0] for s in schemas])
    
    print("\nALL NON-SYSTEM TABLES:")
    tables = conn.execute(text(
        "SELECT table_schema, table_name FROM information_schema.tables "
        "WHERE table_schema NOT IN ('pg_catalog', 'information_schema') "
        "ORDER BY table_schema, table_name;"
    )).fetchall()
    for t in tables:
        print(f"  {t[0]}.{t[1]}")

    alembic_tables = conn.execute(text(
        "SELECT table_schema, table_name FROM information_schema.tables "
        "WHERE table_name LIKE '%alembic%';"
    )).fetchall()
    print("\nALEMBIC TABLES:", alembic_tables)

    test_cols = conn.execute(text(
        "SELECT column_name, data_type, column_default FROM information_schema.columns "
        "WHERE table_schema = 'forge' AND table_name = 'tests' ORDER BY ordinal_position;"
    )).fetchall()
    print("\nCOLUMNS IN 'tests' TABLE:")
    for c in test_cols:
        print(f"  - {c[0]}: {c[1]} (default: {c[2]})")

