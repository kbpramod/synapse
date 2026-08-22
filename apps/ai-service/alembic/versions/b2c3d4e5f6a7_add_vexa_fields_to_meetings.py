"""add_vexa_fields_to_meetings

Revision ID: b2c3d4e5f6a7
Revises: a1b2c3d4e5f6
Create Date: 2026-08-22 12:00:00.000000

"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = 'b2c3d4e5f6a7'
down_revision: Union[str, Sequence[str], None] = 'a1b2c3d4e5f6'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    conn = op.get_bind()
    inspector = sa.inspect(conn)
    columns = [c['name'] for c in inspector.get_columns('meetings')]

    if 'platform' not in columns:
        op.add_column('meetings', sa.Column('platform', sa.String(), nullable=False, server_default='google_meet'))
    if 'meeting_url' not in columns:
        op.add_column('meetings', sa.Column('meeting_url', sa.String(), nullable=True))
    if 'native_meeting_id' not in columns:
        op.add_column('meetings', sa.Column('native_meeting_id', sa.String(), nullable=True))
        op.create_index('ix_meetings_native_meeting_id', 'meetings', ['native_meeting_id'])
    if 'vexa_meeting_id' not in columns:
        op.add_column('meetings', sa.Column('vexa_meeting_id', sa.String(), nullable=True))
        op.create_index('ix_meetings_vexa_meeting_id', 'meetings', ['vexa_meeting_id'])


def downgrade() -> None:
    conn = op.get_bind()
    inspector = sa.inspect(conn)
    columns = [c['name'] for c in inspector.get_columns('meetings')]

    if 'vexa_meeting_id' in columns:
        op.drop_index('ix_meetings_vexa_meeting_id', table_name='meetings')
        op.drop_column('meetings', 'vexa_meeting_id')
    if 'native_meeting_id' in columns:
        op.drop_index('ix_meetings_native_meeting_id', table_name='meetings')
        op.drop_column('meetings', 'native_meeting_id')
    if 'meeting_url' in columns:
        op.drop_column('meetings', 'meeting_url')
    if 'platform' in columns:
        op.drop_column('meetings', 'platform')
