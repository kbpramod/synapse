"""Initial database schema with websites, accounts, pages, elements, tests, test_runs, and heals.

Revision ID: 0001
Revises:
Create Date: 2026-09-05 10:20:00.000000

"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = '0001'
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    conn = op.get_bind()
    inspector = sa.inspect(conn)
    existing_tables = set(inspector.get_table_names(schema='forge'))

    # 1. Websites Table
    if 'websites' not in existing_tables:
        op.create_table(
            'websites',
            sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
            sa.Column('url', sa.Text(), nullable=False),
            sa.Column('domain', sa.String(length=255), nullable=False),
            sa.Column('is_active', sa.Boolean(), server_default=sa.text('true'), nullable=False),
            sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('NOW()'), nullable=False),
            sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('NOW()'), nullable=False),
            sa.Column('last_discovered_at', sa.DateTime(timezone=True), nullable=True),
            sa.PrimaryKeyConstraint('id'),
            sa.UniqueConstraint('url'),
        )
        op.create_index('idx_websites_url', 'websites', ['url'])
        op.create_index('idx_websites_domain', 'websites', ['domain'])
        op.create_index('idx_websites_is_active', 'websites', ['is_active'])
    else:
        # Reconcile columns if websites was created in an earlier prototype
        w_cols = [c['name'] for c in inspector.get_columns('websites', schema='forge')]
        if 'start_url' in w_cols and 'url' not in w_cols:
            op.alter_column('websites', 'start_url', new_column_name='url')
        if 'is_active' not in w_cols:
            op.add_column('websites', sa.Column('is_active', sa.Boolean(), server_default=sa.text('true'), nullable=False))
        if 'updated_at' not in w_cols:
            op.add_column('websites', sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('NOW()'), nullable=False))
        w_constrs = [c['name'] for c in inspector.get_unique_constraints('websites', schema='forge')]
        if 'uq_websites_url' not in w_constrs:
            try:
                op.create_unique_constraint('uq_websites_url', 'websites', ['url'])
            except Exception:
                pass

    # 2. Accounts Table (one-to-many with websites)
    if 'accounts' not in existing_tables:
        op.create_table(
            'accounts',
            sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
            sa.Column('website_id', sa.Integer(), nullable=False),
            sa.Column('username', sa.String(length=255), nullable=False),
            sa.Column('password', sa.Text(), nullable=False),
            sa.Column('role', sa.String(length=50), server_default='user', nullable=False),
            sa.Column('credentials', postgresql.JSONB(astext_type=sa.Text()), server_default=sa.text("'{}'::jsonb"), nullable=False),
            sa.Column('is_active', sa.Boolean(), server_default=sa.text('true'), nullable=False),
            sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('NOW()'), nullable=False),
            sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('NOW()'), nullable=False),
            sa.ForeignKeyConstraint(['website_id'], ['websites.id'], ondelete='CASCADE'),
            sa.PrimaryKeyConstraint('id'),
            sa.UniqueConstraint('website_id', 'username', name='uq_website_account_username'),
        )
        op.create_index('idx_accounts_website_id', 'accounts', ['website_id'])
        op.create_index('idx_accounts_role', 'accounts', ['role'])
        op.create_index('idx_accounts_is_active', 'accounts', ['is_active'])

    # 3. Discovered Pages Table
    if 'pages' not in existing_tables:
        op.create_table(
            'pages',
            sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
            sa.Column('domain', sa.String(length=255), nullable=False),
            sa.Column('url', sa.Text(), nullable=False),
            sa.Column('title', sa.Text(), nullable=True),
            sa.Column('slug', sa.String(length=255), nullable=True),
            sa.Column('page_type', sa.String(length=100), nullable=True),
            sa.Column('purpose', sa.Text(), nullable=True),
            sa.Column('primary_actions', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
            sa.Column('state_preconditions', sa.Text(), nullable=True),
            sa.Column('discovered_at', sa.DateTime(timezone=True), server_default=sa.text('NOW()'), nullable=False),
            sa.PrimaryKeyConstraint('id'),
            sa.UniqueConstraint('url'),
        )
        op.create_index('idx_pages_domain', 'pages', ['domain'])

    # 4. Interactive Elements Table
    if 'elements' not in existing_tables:
        op.create_table(
            'elements',
            sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
            sa.Column('forge_id', sa.String(length=100), nullable=False),
            sa.Column('page_url', sa.Text(), nullable=False),
            sa.Column('tag', sa.String(length=50), nullable=False),
            sa.Column('element_type', sa.String(length=50), nullable=True),
            sa.Column('text', sa.Text(), nullable=True),
            sa.Column('selector', sa.Text(), nullable=False),
            sa.Column('bounding_box', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
            sa.Column('discovered_at', sa.DateTime(timezone=True), server_default=sa.text('NOW()'), nullable=False),
            sa.PrimaryKeyConstraint('id'),
            sa.UniqueConstraint('forge_id', 'page_url', name='uq_forge_element'),
        )
        op.create_index('idx_elements_forge_id', 'elements', ['forge_id'])

    # 5. Planned & Generated Tests Table
    if 'tests' not in existing_tables:
        op.create_table(
            'tests',
            sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
            sa.Column('test_id', sa.String(length=100), nullable=False),
            sa.Column('domain', sa.String(length=255), nullable=False),
            sa.Column('page_url', sa.Text(), nullable=True),
            sa.Column('title', sa.Text(), nullable=False),
            sa.Column('description', sa.Text(), nullable=True),
            sa.Column('category', sa.String(length=50), server_default='regression', nullable=False),
            sa.Column('priority', sa.String(length=20), server_default='medium', nullable=False),
            sa.Column('steps', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
            sa.Column('expected_outcome', sa.Text(), nullable=True),
            sa.Column('script_path', sa.Text(), nullable=True),
            sa.Column('test_code', sa.Text(), nullable=True),
            sa.Column('language', sa.String(length=20), server_default='python', nullable=False),
            sa.Column('status', sa.String(length=50), server_default='active', nullable=False),
            sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('NOW()'), nullable=False),
            sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('NOW()'), nullable=False),
            sa.PrimaryKeyConstraint('id'),
            sa.UniqueConstraint('test_id'),
        )
        op.create_index('idx_tests_domain', 'tests', ['domain'])
        op.create_index('idx_tests_status', 'tests', ['status'])

    # 6. Test Run Executions Table
    if 'test_runs' not in existing_tables:
        op.create_table(
            'test_runs',
            sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
            sa.Column('run_id', sa.String(length=100), nullable=False),
            sa.Column('test_id', sa.String(length=100), nullable=False),
            sa.Column('exit_code', sa.Integer(), nullable=False),
            sa.Column('status', sa.String(length=50), nullable=False),
            sa.Column('duration_s', sa.Float(), server_default='0.0', nullable=False),
            sa.Column('error_summary', sa.Text(), nullable=True),
            sa.Column('stdout', sa.Text(), nullable=True),
            sa.Column('stderr', sa.Text(), nullable=True),
            sa.Column('screenshot_paths', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
            sa.Column('trace_path', sa.Text(), nullable=True),
            sa.Column('executed_at', sa.DateTime(timezone=True), server_default=sa.text('NOW()'), nullable=False),
            sa.PrimaryKeyConstraint('id'),
        )
        op.create_index('idx_runs_test_id', 'test_runs', ['test_id'])
        op.create_index('idx_runs_executed_at', 'test_runs', ['executed_at'])

    # 7. Self-Healing Audit Trail Table
    if 'heals' not in existing_tables:
        op.create_table(
            'heals',
            sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
            sa.Column('test_id', sa.String(length=100), nullable=False),
            sa.Column('run_id', sa.String(length=100), nullable=True),
            sa.Column('attempt', sa.Integer(), server_default='1', nullable=False),
            sa.Column('error_snippet', sa.Text(), nullable=True),
            sa.Column('diagnosis', sa.Text(), nullable=False),
            sa.Column('fix_plan', sa.Text(), nullable=False),
            sa.Column('healed_at', sa.DateTime(timezone=True), server_default=sa.text('NOW()'), nullable=False),
            sa.PrimaryKeyConstraint('id'),
        )


def downgrade() -> None:
    op.drop_table('heals')
    op.drop_table('test_runs')
    op.drop_table('tests')
    op.drop_table('elements')
    op.drop_table('pages')
    op.drop_table('accounts')
    op.drop_table('websites')
