import os
from sqlalchemy import create_engine, text
from dotenv import load_dotenv

load_dotenv()
db_url = os.getenv("DATABASE_URL")
if db_url and db_url.startswith("postgres://"):
    db_url = db_url.replace("postgres://", "postgresql://", 1)

engine = create_engine(db_url)
with engine.connect() as conn:
    for tbl in ["organizations", "organization_members", "github_installations", "repositories", "github_repositories", "doc_pages"]:
        try:
            res = conn.execute(text(f"SELECT count(*) FROM {tbl}")).scalar()
            print(f"Row count for {tbl}: {res}")
        except Exception as e:
            print(f"Error for {tbl}: {e}")
