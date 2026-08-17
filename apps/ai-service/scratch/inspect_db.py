import os
import sys
from dotenv import load_dotenv
import sqlalchemy as sa

sys.path.insert(0, ".")
load_dotenv()

from src.db.database import engine

insp = sa.inspect(engine)
tables = insp.get_table_names()
print("Existing Tables in DB:", tables)

with engine.connect() as conn:
    res = conn.execute(sa.text("SELECT typname FROM pg_type WHERE typnamespace = 2200")).fetchall()
    print("Types in namespace 2200:", [r[0] for r in res])
