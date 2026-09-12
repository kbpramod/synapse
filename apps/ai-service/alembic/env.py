import os
import sys
from logging.config import fileConfig

from sqlalchemy import engine_from_config, pool
from alembic import context
from dotenv import load_dotenv

# Ensure root directory of ai-service is in sys.path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

# Load environment variables
load_dotenv()

# Import Base and all SQLAlchemy models for autogenerate support
from src.db.database import Base
from models.user import User
from models.organization import Organization
from models.organization_member import OrganizationMember
from models.project import Project
from models.repository import Repository, GithubRepository
from models.doc_page import DocPage
from models.github_install_state import GithubInstallState
from models.github_installation import GithubInstallation
from models.knowledge_node import KnowledgeNode
from models.person import Person, IdentityLink
from models.work_item import WorkItem
from models.event_node import EventNode
from models.event_relationship import EventRelationship
from models.github_event import GithubEvent
from models.github_user import GithubUser
from models.pull_request import PullRequest
from models.commit import Commit
from models.fact import Fact
from models.meeting import Meeting

# Alembic Config object
config = context.config

# Set sqlalchemy.url dynamically from DATABASE_URL if available
database_url = os.getenv("DATABASE_URL")
if database_url:
    # Ensure postgresql:// schema if postgres:// is provided
    if database_url.startswith("postgres://"):
        database_url = database_url.replace("postgres://", "postgresql://", 1)
    config.set_main_option("sqlalchemy.url", database_url)

# Interpret config file for Python logging
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata


def run_migrations_offline() -> None:
    """Run migrations in 'offline' mode."""
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )

    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """Run migrations in 'online' mode."""
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )

    with connectable.connect() as connection:
        context.configure(
            connection=connection, target_metadata=target_metadata
        )

        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
