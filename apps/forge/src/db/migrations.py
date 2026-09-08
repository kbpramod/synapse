import logging
from pathlib import Path
from alembic import command
from alembic.config import Config

logger = logging.getLogger("forge.db.migrations")


def get_alembic_config() -> Config:
    """Returns the Alembic Config pointing to backend/alembic.ini."""
    backend_root = Path(__file__).resolve().parent.parent.parent
    ini_path = backend_root / "alembic.ini"
    if not ini_path.exists():
        raise FileNotFoundError(f"Alembic configuration file not found at {ini_path}")
    return Config(str(ini_path))


def init_db() -> str:
    """
    Applies all pending Alembic migrations up to 'head'.
    Returns the target revision ('head').
    """
    logger.info("Applying database migrations with Alembic...")
    alembic_cfg = get_alembic_config()
    command.upgrade(alembic_cfg, "head")
    logger.info("Alembic database migrations applied successfully.")
    return "head"
