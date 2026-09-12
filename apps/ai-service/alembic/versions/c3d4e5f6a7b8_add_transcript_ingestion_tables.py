"""add_transcript_ingestion_tables

Revision ID: c3d4e5f6a7b8
Revises: b2c3d4e5f6a7
Create Date: 2026-09-12 12:00:00.000000

"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'c3d4e5f6a7b8'
down_revision: Union[str, Sequence[str], None] = 'b2c3d4e5f6a7'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    conn = op.get_bind()
    inspector = sa.inspect(conn)
    columns = [c['name'] for c in inspector.get_columns('meetings')]

    # 1. Add new columns to meetings
    if 'source_type' not in columns:
        op.add_column('meetings', sa.Column('source_type', sa.String(), nullable=True, server_default='pasted'))
    if 'storage_key' not in columns:
        op.add_column('meetings', sa.Column('storage_key', sa.String(), nullable=True))
    if 'transcript_text' not in columns:
        op.add_column('meetings', sa.Column('transcript_text', sa.Text(), nullable=True))
    if 'analyzed_at' not in columns:
        op.add_column('meetings', sa.Column('analyzed_at', sa.DateTime(), nullable=True))
    if 'analysis_status' not in columns:
        op.add_column('meetings', sa.Column('analysis_status', sa.String(), nullable=False, server_default='pending'))
        op.create_index('ix_meetings_analysis_status', 'meetings', ['analysis_status'])

    # 2. Create decisions table
    tables = inspector.get_table_names()
    if 'decisions' not in tables:
        op.create_table(
            'decisions',
            sa.Column('id', sa.String(), primary_key=True),
            sa.Column('meeting_id', sa.String(), sa.ForeignKey('meetings.id', ondelete='CASCADE'), nullable=False),
            sa.Column('decision', sa.Text(), nullable=False),
            sa.Column('rationale', sa.Text(), nullable=True),
            sa.Column('participants', sa.JSON(), nullable=False),
            sa.Column('source_reference', sa.JSON(), nullable=False),
            sa.Column('created_at', sa.DateTime(), nullable=False),
            sa.Column('updated_at', sa.DateTime(), nullable=False),
        )
        op.create_index('ix_decisions_meeting_id', 'decisions', ['meeting_id'])

    # 3. Create tasks table
    if 'tasks' not in tables:
        op.create_table(
            'tasks',
            sa.Column('id', sa.String(), primary_key=True),
            sa.Column('meeting_id', sa.String(), sa.ForeignKey('meetings.id', ondelete='CASCADE'), nullable=False),
            sa.Column('title', sa.String(), nullable=False),
            sa.Column('description', sa.Text(), nullable=True),
            sa.Column('owner', sa.String(), nullable=True),
            sa.Column('deadline', sa.String(), nullable=True),
            sa.Column('status', sa.String(), nullable=False, server_default='open'),
            sa.Column('source_reference', sa.JSON(), nullable=False),
            sa.Column('created_at', sa.DateTime(), nullable=False),
            sa.Column('updated_at', sa.DateTime(), nullable=False),
        )
        op.create_index('ix_tasks_meeting_id', 'tasks', ['meeting_id'])

    # 4. Create knowledge table
    if 'knowledge' not in tables:
        op.create_table(
            'knowledge',
            sa.Column('id', sa.String(), primary_key=True),
            sa.Column('meeting_id', sa.String(), sa.ForeignKey('meetings.id', ondelete='CASCADE'), nullable=False),
            sa.Column('topic', sa.String(), nullable=False),
            sa.Column('content', sa.Text(), nullable=False),
            sa.Column('source_reference', sa.JSON(), nullable=False),
            sa.Column('created_at', sa.DateTime(), nullable=False),
            sa.Column('updated_at', sa.DateTime(), nullable=False),
        )
        op.create_index('ix_knowledge_meeting_id', 'knowledge', ['meeting_id'])


def downgrade() -> None:
    op.drop_table('knowledge')
    op.drop_table('tasks')
    op.drop_table('decisions')
    op.drop_index('ix_meetings_analysis_status', table_name='meetings')
    op.drop_column('meetings', 'analysis_status')
    op.drop_column('meetings', 'analyzed_at')
    op.drop_column('meetings', 'transcript_text')
    op.drop_column('meetings', 'storage_key')
    op.drop_column('meetings', 'source_type')
