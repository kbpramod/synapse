import os
from sqlalchemy import create_engine, inspect
from dotenv import load_dotenv

load_dotenv()
db_url = os.getenv("DATABASE_URL")
if db_url and db_url.startswith("postgres://"):
    db_url = db_url.replace("postgres://", "postgresql://", 1)

engine = create_engine(db_url)
inspector = inspect(engine)

for table in ["github_installations", "repositories", "doc_pages"]:
    if table in inspector.get_table_names():
        print(f"\n--- {table} ---")
        print("Columns:", [c["name"] for c in inspector.get_columns(table)])
        print("Indexes:", [ix["name"] for ix in inspector.get_indexes(table)])
        print("FKs:", [fk["name"] for fk in inspector.get_foreign_keys(table)])
