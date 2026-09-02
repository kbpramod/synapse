import logging
from typing import Generator
from contextlib import contextmanager
from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine, Connection
from config import DATABASE_URL

logger = logging.getLogger("forge.db")

_engine: Engine | None = None


def get_engine() -> Engine:
    """Returns a singleton SQLAlchemy engine connected to Neon PostgreSQL."""
    global _engine
    if _engine is None:
        if not DATABASE_URL:
            raise ValueError(
                "DATABASE_URL is not set in environment or .env file. "
                "Please configure a valid PostgreSQL connection string."
            )
        # Ensure pooling settings suitable for serverless Neon Postgres
        _engine = create_engine(
            DATABASE_URL,
            pool_pre_ping=True,
            pool_recycle=300,
            pool_size=5,
            max_overflow=10,
        )
    return _engine


@contextmanager
def get_connection() -> Generator[Connection, None, None]:
    """
    Context manager that yields an active database connection with search_path
    set to forge, public for safety.
    """
    engine = get_engine()
    with engine.begin() as conn:
        # Guarantee we operate safely within the forge schema
        conn.execute(text("CREATE SCHEMA IF NOT EXISTS forge;"))
        conn.execute(text("SET search_path TO forge, public;"))
        yield conn
