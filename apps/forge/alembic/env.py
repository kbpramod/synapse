import os
import sys
from logging.config import fileConfig
from pathlib import Path

from sqlalchemy import engine_from_config, pool, text
from alembic import context

# 1. Ensure backend/src is on sys.path
backend_dir = Path(__file__).resolve().parent.parent
src_dir = backend_dir / "src"
if str(src_dir) not in sys.path:
    sys.path.insert(0, str(src_dir))

# 2. Import application configuration and models
from config import DATABASE_URL  # noqa: E402
from models import Base  # noqa: E402

# 3. Alembic Config object
config = context.config

# Interpret the config file for Python logging.
# disable_existing_loggers=False is required here: this runs on every app startup
# (init_db() -> command.upgrade() -> this file), and fileConfig()'s default of True
# silently disables every logger not explicitly listed in alembic.ini's [loggers]
# section — including every forge.* pipeline logger — wiping out the app's own
# logging setup a few milliseconds after it runs.
if config.config_file_name is not None:
    fileConfig(config.config_file_name, disable_existing_loggers=False)

# 4. Set SQLAlchemy target metadata
target_metadata = Base.metadata

# 5. Ensure database URL is sourced from config / environment
if DATABASE_URL:
    # Ensure URL is compatible with standard psycopg2 driver
    db_url = DATABASE_URL
    if db_url.startswith("postgres://"):
        db_url = db_url.replace("postgres://", "postgresql://", 1)
    config.set_main_option("sqlalchemy.url", db_url)


def run_migrations_offline() -> None:
    """Run migrations in 'offline' mode."""
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
        version_table="alembic_version",
        version_table_schema="forge",
        include_schemas=True,
    )

    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """Run migrations in 'online' mode."""
    configuration = config.get_section(config.config_ini_section, {})
    if DATABASE_URL:
        db_url = DATABASE_URL
        if db_url.startswith("postgres://"):
            db_url = db_url.replace("postgres://", "postgresql://", 1)
        configuration["sqlalchemy.url"] = db_url

    connectable = engine_from_config(
        configuration,
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )

    with connectable.connect() as connection:
        # Ensure schema exists and enforce search_path to forge, public
        connection.execute(text("CREATE SCHEMA IF NOT EXISTS forge;"))
        connection.execute(text("SET search_path TO forge, public;"))
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            compare_type=True,
            version_table="alembic_version",
            version_table_schema="forge",
            include_schemas=True,
        )

        with context.begin_transaction():
            context.run_migrations()
        connection.commit()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
