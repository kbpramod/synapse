"""Add cron timings and website_id reference to tests table.

Revision ID: 0002
Revises: 0001
Create Date: 2026-09-05 10:45:00.000000

"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = '0002'
down_revision: Union[str, None] = '0001'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    conn = op.get_bind()
    inspector = sa.inspect(conn)
    columns = [c['name'] for c in inspector.get_columns('tests', schema='forge')]

    # Add website_id reference if not present
    if 'website_id' not in columns:
        op.add_column(
            'tests',
            sa.Column('website_id', sa.Integer(), nullable=True)
        )
        op.create_foreign_key(
            'fk_tests_website_id',
            'tests',
            'websites',
            ['website_id'],
            ['id'],
            ondelete='CASCADE'
        )
        op.create_index('idx_tests_website_id', 'tests', ['website_id'])

    # Add cron_interval_hours (e.g. run test every N hours)
    if 'cron_interval_hours' not in columns:
        op.add_column(
            'tests',
            sa.Column('cron_interval_hours', sa.Integer(), server_default='24', nullable=True)
        )
        op.create_index('idx_tests_cron_interval', 'tests', ['cron_interval_hours'])

    # Add cron_expression (e.g. '0 */6 * * *')
    if 'cron_expression' not in columns:
        op.add_column(
            'tests',
            sa.Column('cron_expression', sa.String(length=100), server_default='0 0 * * *', nullable=True)
        )

    # Add last_run_at timestamp
    if 'last_run_at' not in columns:
        op.add_column(
            'tests',
            sa.Column('last_run_at', sa.DateTime(timezone=True), nullable=True)
        )

    # Add next_run_at timestamp
    if 'next_run_at' not in columns:
        op.add_column(
            'tests',
            sa.Column('next_run_at', sa.DateTime(timezone=True), nullable=True)
        )
        op.create_index('idx_tests_next_run_at', 'tests', ['next_run_at'])


def downgrade() -> None:
    op.drop_index('idx_tests_next_run_at', table_name='tests')
    op.drop_column('tests', 'next_run_at')
    op.drop_column('tests', 'last_run_at')
    op.drop_column('tests', 'cron_expression')
    op.drop_index('idx_tests_cron_interval', table_name='tests')
    op.drop_column('tests', 'cron_interval_hours')
    op.drop_constraint('fk_tests_website_id', 'tests', type_='foreignkey')
    op.drop_index('idx_tests_website_id', table_name='tests')
    op.drop_column('tests', 'website_id')
