"""Add app_name and environment to websites table.

Revision ID: 0003
Revises: 0002
Create Date: 2026-09-08 17:35:00.000000

"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = '0003'
down_revision: Union[str, None] = '0002'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    conn = op.get_bind()
    inspector = sa.inspect(conn)
    columns = [c['name'] for c in inspector.get_columns('websites', schema='forge')]

    # Add app_name column if not present
    if 'app_name' not in columns:
        op.add_column(
            'websites',
            sa.Column('app_name', sa.String(length=255), nullable=True)
        )

    # Add environment column if not present
    if 'environment' not in columns:
        op.add_column(
            'websites',
            sa.Column('environment', sa.String(length=50), server_default='Production', nullable=True)
        )
        op.create_index('idx_websites_environment', 'websites', ['environment'])


def downgrade() -> None:
    op.drop_index('idx_websites_environment', table_name='websites')
    op.drop_column('websites', 'environment')
    op.drop_column('websites', 'app_name')
