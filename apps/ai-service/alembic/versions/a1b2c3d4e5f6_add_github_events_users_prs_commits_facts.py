"""add_github_events_users_prs_commits_facts

Revision ID: a1b2c3d4e5f6
Revises: fbd566a32ac2
Create Date: 2026-08-17 18:31:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql
from pgvector.sqlalchemy import Vector

# revision identifiers, used by Alembic.
revision: str = 'a1b2c3d4e5f6'
down_revision: Union[str, Sequence[str], None] = 'fbd566a32ac2'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    conn = op.get_bind()
    inspector = sa.inspect(conn)
    existing_tables = inspector.get_table_names()

    # 1. Create github_events table
    if 'github_events' not in existing_tables:
        op.create_table(
            'github_events',
            sa.Column('id', sa.String(), primary_key=True, nullable=False),
            sa.Column('installation_id', sa.String(), sa.ForeignKey('github_installations.id', ondelete='SET NULL'), nullable=True),
            sa.Column('repository_id', sa.String(), sa.ForeignKey('github_repositories.id', ondelete='SET NULL'), nullable=True),
            sa.Column('event_type', sa.String(), nullable=False),
            sa.Column('github_event_id', sa.String(), nullable=True),
            sa.Column('github_created_at', sa.DateTime(), server_default=sa.text('now()'), nullable=False),
            sa.Column('received_at', sa.DateTime(), server_default=sa.text('now()'), nullable=False),
            sa.Column('payload', sa.JSON(), nullable=False)
        )
        op.create_index('ix_github_events_installation_id', 'github_events', ['installation_id'])
        op.create_index('ix_github_events_repository_id', 'github_events', ['repository_id'])
        op.create_index('ix_github_events_event_type', 'github_events', ['event_type'])
        op.create_index('ix_github_events_github_event_id', 'github_events', ['github_event_id'])

    # 2. Create github_users table
    if 'github_users' not in existing_tables:
        op.create_table(
            'github_users',
            sa.Column('id', sa.String(), primary_key=True, nullable=False),
            sa.Column('github_user_id', sa.String(), unique=True, nullable=False),
            sa.Column('username', sa.String(), nullable=False),
            sa.Column('display_name', sa.String(), nullable=True),
            sa.Column('email', sa.String(), nullable=True),
            sa.Column('avatar_url', sa.String(), nullable=True),
            sa.Column('person_id', sa.String(), sa.ForeignKey('persons.id', ondelete='SET NULL'), nullable=True),
            sa.Column('created_at', sa.DateTime(), server_default=sa.text('now()'), nullable=False),
            sa.Column('updated_at', sa.DateTime(), server_default=sa.text('now()'), nullable=False)
        )
        op.create_index('ix_github_users_github_user_id', 'github_users', ['github_user_id'])
        op.create_index('ix_github_users_username', 'github_users', ['username'])
        op.create_index('ix_github_users_email', 'github_users', ['email'])
        op.create_index('ix_github_users_person_id', 'github_users', ['person_id'])

    # 3. Create pull_requests table
    if 'pull_requests' not in existing_tables:
        op.create_table(
            'pull_requests',
            sa.Column('id', sa.String(), primary_key=True, nullable=False),
            sa.Column('repository_id', sa.String(), sa.ForeignKey('github_repositories.id', ondelete='CASCADE'), nullable=False),
            sa.Column('github_pr_id', sa.String(), unique=True, nullable=False),
            sa.Column('number', sa.Integer(), nullable=False),
            sa.Column('title', sa.String(), nullable=False),
            sa.Column('description', sa.Text(), nullable=True),
            sa.Column('author_github_user_id', sa.String(), sa.ForeignKey('github_users.id', ondelete='SET NULL'), nullable=True),
            sa.Column('source_branch', sa.String(), nullable=False),
            sa.Column('target_branch', sa.String(), nullable=False),
            sa.Column('head_sha', sa.String(), nullable=True),
            sa.Column('base_sha', sa.String(), nullable=True),
            sa.Column('status', sa.String(), server_default='open', nullable=False),
            sa.Column('opened_at', sa.DateTime(), nullable=True),
            sa.Column('updated_at', sa.DateTime(), nullable=True),
            sa.Column('closed_at', sa.DateTime(), nullable=True),
            sa.Column('merged_at', sa.DateTime(), nullable=True),
            sa.Column('created_at', sa.DateTime(), server_default=sa.text('now()'), nullable=False),
            sa.Column('updated_at_local', sa.DateTime(), server_default=sa.text('now()'), nullable=False)
        )
        op.create_index('ix_pull_requests_repository_id', 'pull_requests', ['repository_id'])
        op.create_index('ix_pull_requests_github_pr_id', 'pull_requests', ['github_pr_id'])
        op.create_index('ix_pull_requests_number', 'pull_requests', ['number'])
        op.create_index('ix_pull_requests_author_github_user_id', 'pull_requests', ['author_github_user_id'])
        op.create_index('ix_pull_requests_status', 'pull_requests', ['status'])

    # 4. Create commits table
    if 'commits' not in existing_tables:
        op.create_table(
            'commits',
            sa.Column('id', sa.String(), primary_key=True, nullable=False),
            sa.Column('pull_request_id', sa.String(), sa.ForeignKey('pull_requests.id', ondelete='CASCADE'), nullable=True),
            sa.Column('repository_id', sa.String(), sa.ForeignKey('github_repositories.id', ondelete='CASCADE'), nullable=True),
            sa.Column('github_commit_sha', sa.String(), nullable=False),
            sa.Column('author_github_user_id', sa.String(), sa.ForeignKey('github_users.id', ondelete='SET NULL'), nullable=True),
            sa.Column('message', sa.Text(), nullable=True),
            sa.Column('committed_at', sa.DateTime(), nullable=True),
            sa.Column('created_at', sa.DateTime(), server_default=sa.text('now()'), nullable=False)
        )
        op.create_index('ix_commits_pull_request_id', 'commits', ['pull_request_id'])
        op.create_index('ix_commits_repository_id', 'commits', ['repository_id'])
        op.create_index('ix_commits_github_commit_sha', 'commits', ['github_commit_sha'])
        op.create_index('ix_commits_author_github_user_id', 'commits', ['author_github_user_id'])

    # 5. Create facts table
    if 'facts' not in existing_tables:
        op.create_table(
            'facts',
            sa.Column('id', sa.String(), primary_key=True, nullable=False),
            sa.Column('content', sa.Text(), nullable=False),
            sa.Column('embedding', Vector(1536), nullable=True),
            sa.Column('source_type', sa.String(), nullable=False),
            sa.Column('source_id', sa.String(), nullable=False),
            sa.Column('person_id', sa.String(), sa.ForeignKey('persons.id', ondelete='SET NULL'), nullable=True),
            sa.Column('repository_id', sa.String(), sa.ForeignKey('github_repositories.id', ondelete='CASCADE'), nullable=True),
            sa.Column('fact_status', sa.String(), server_default='ACTIVE', nullable=False),
            sa.Column('work_status', sa.String(), server_default='IN_PROGRESS', nullable=False),
            sa.Column('metadata_json', sa.JSON(), nullable=True),
            sa.Column('created_at', sa.DateTime(), server_default=sa.text('now()'), nullable=False),
            sa.Column('updated_at', sa.DateTime(), server_default=sa.text('now()'), nullable=False)
        )
        op.create_index('ix_facts_source_type', 'facts', ['source_type'])
        op.create_index('ix_facts_source_id', 'facts', ['source_id'])
        op.create_index('ix_facts_person_id', 'facts', ['person_id'])
        op.create_index('ix_facts_repository_id', 'facts', ['repository_id'])
        op.create_index('ix_facts_fact_status', 'facts', ['fact_status'])
        op.create_index('ix_facts_work_status', 'facts', ['work_status'])


def downgrade() -> None:
    op.drop_table('facts')
    op.drop_table('commits')
    op.drop_table('pull_requests')
    op.drop_table('github_users')
    op.drop_table('github_events')
