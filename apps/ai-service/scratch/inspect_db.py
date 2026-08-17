import os
from sqlalchemy import create_engine, inspect
from dotenv import load_dotenv

load_dotenv()
db_url = os.getenv("DATABASE_URL")
if db_url and db_url.startswith("postgres://"):
    db_url = db_url.replace("postgres://", "postgresql://", 1)

engine = create_engine(db_url)
inspector = inspect(engine)
tables = inspector.get_table_names()
print("Found tables:", tables)
for table in sorted(tables):
    print(f"\nTABLE: {table}")
    for col in inspector.get_columns(table):
        print(f"  {col['name']}: {col['type']} (nullable={col['nullable']})")
