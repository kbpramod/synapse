"""add_organizations_and_update_github_entities

Revision ID: fbd566a32ac2
Revises: dfe82a32a657
Create Date: 2026-08-17 17:17:47.884354

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = 'fbd566a32ac2'
down_revision: Union[str, Sequence[str], None] = 'dfe82a32a657'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # 1. Create organizations table
    op.create_table(
        'organizations',
        sa.Column('id', sa.String(), primary_key=True, nullable=False),
        sa.Column('name', sa.String(), nullable=False),
        sa.Column('created_at', sa.DateTime(), nullable=False, server_default=sa.text('now()')),
        sa.Column('updated_at', sa.DateTime(), nullable=False, server_default=sa.text('now()')),
    )

    # 2. Create organization_members table
    op.create_table(
        'organization_members',
        sa.Column('id', sa.String(), primary_key=True, nullable=False),
        sa.Column('organization_id', sa.String(), sa.ForeignKey('organizations.id', ondelete='CASCADE'), nullable=False),
        sa.Column('user_id', sa.String(), sa.ForeignKey('users.id', ondelete='CASCADE'), nullable=False),
        sa.Column('role', sa.String(), nullable=False, server_default='member'),
        sa.Column('created_at', sa.DateTime(), nullable=False, server_default=sa.text('now()')),
        sa.Column('updated_at', sa.DateTime(), nullable=False, server_default=sa.text('now()')),
        sa.UniqueConstraint('organization_id', 'user_id', name='uq_org_member')
    )
    op.create_index('ix_organization_members_organization_id', 'organization_members', ['organization_id'])
    op.create_index('ix_organization_members_user_id', 'organization_members', ['user_id'])

    # 3. Update github_installations table
    op.drop_constraint('github_installations_user_id_fkey', 'github_installations', type_='foreignkey')
    op.drop_index('ix_github_installations_installation_id', table_name='github_installations')

    op.add_column('github_installations', sa.Column('organization_id', sa.String(), nullable=True))
    op.add_column('github_installations', sa.Column('github_installation_id', sa.String(), nullable=True))
    op.add_column('github_installations', sa.Column('github_account_id', sa.String(), nullable=True))
    op.add_column('github_installations', sa.Column('github_account_login', sa.String(), nullable=True))
    op.add_column('github_installations', sa.Column('github_account_type', sa.String(), nullable=True))
    op.add_column('github_installations', sa.Column('installed_at', sa.DateTime(), nullable=True))
    op.add_column('github_installations', sa.Column('uninstalled_at', sa.DateTime(), nullable=True))
    op.add_column('github_installations', sa.Column('status', sa.String(), nullable=False, server_default='active'))

    # Copy data from old columns
    op.execute("UPDATE github_installations SET github_installation_id = installation_id")
    op.execute("UPDATE github_installations SET github_account_id = account_id")
    op.execute("UPDATE github_installations SET github_account_login = account_login")
    op.execute("UPDATE github_installations SET github_account_type = account_type")
    op.execute("UPDATE github_installations SET installed_at = created_at")

    op.alter_column('github_installations', 'github_installation_id', nullable=False)
    op.alter_column('github_installations', 'user_id', existing_type=sa.String(), nullable=True)

    op.drop_column('github_installations', 'installation_id')
    op.drop_column('github_installations', 'account_id')
    op.drop_column('github_installations', 'account_login')
    op.drop_column('github_installations', 'account_type')

    op.create_index('ix_github_installations_github_installation_id', 'github_installations', ['github_installation_id'], unique=True)
    op.create_index('ix_github_installations_organization_id', 'github_installations', ['organization_id'])
    op.create_foreign_key('fk_github_installations_user_id', 'github_installations', 'users', ['user_id'], ['id'], ondelete='SET NULL')
    op.create_foreign_key('fk_github_installations_organization_id', 'github_installations', 'organizations', ['organization_id'], ['id'], ondelete='CASCADE')

    # 4. Migrate repositories -> github_repositories
    op.drop_constraint('doc_pages_repository_id_fkey', 'doc_pages', type_='foreignkey')
    op.rename_table('repositories', 'github_repositories')
    op.alter_column('github_repositories', 'github_installation_id', new_column_name='installation_id')
    op.add_column('github_repositories', sa.Column('private', sa.Boolean(), nullable=False, server_default=sa.false()))
    op.add_column('github_repositories', sa.Column('active', sa.Boolean(), nullable=False, server_default=sa.true()))
    op.alter_column('github_repositories', 'user_id', existing_type=sa.String(), nullable=True)
    op.drop_constraint('repositories_user_id_fkey', 'github_repositories', type_='foreignkey')
    op.create_foreign_key('fk_github_repositories_user_id', 'github_repositories', 'users', ['user_id'], ['id'], ondelete='SET NULL')
    op.create_foreign_key('doc_pages_repository_id_fkey', 'doc_pages', 'github_repositories', ['repository_id'], ['id'], ondelete='CASCADE')


def downgrade() -> None:
    # Revert doc_pages FK
    op.drop_constraint('doc_pages_repository_id_fkey', 'doc_pages', type_='foreignkey')

    # Revert github_repositories
    op.drop_constraint('fk_github_repositories_user_id', 'github_repositories', type_='foreignkey')
    op.create_foreign_key('repositories_user_id_fkey', 'github_repositories', 'users', ['user_id'], ['id'], ondelete='CASCADE')
    op.drop_column('github_repositories', 'active')
    op.drop_column('github_repositories', 'private')
    op.alter_column('github_repositories', 'installation_id', new_column_name='github_installation_id')
    op.rename_table('github_repositories', 'repositories')
    op.create_foreign_key('doc_pages_repository_id_fkey', 'doc_pages', 'repositories', ['repository_id'], ['id'], ondelete='CASCADE')

    # Revert github_installations
    op.drop_constraint('fk_github_installations_organization_id', 'github_installations', type_='foreignkey')
    op.drop_constraint('fk_github_installations_user_id', 'github_installations', type_='foreignkey')
    op.drop_index('ix_github_installations_organization_id', table_name='github_installations')
    op.drop_index('ix_github_installations_github_installation_id', table_name='github_installations')

    op.add_column('github_installations', sa.Column('installation_id', sa.String(), nullable=True))
    op.add_column('github_installations', sa.Column('account_id', sa.String(), nullable=True))
    op.add_column('github_installations', sa.Column('account_login', sa.String(), nullable=True))
    op.add_column('github_installations', sa.Column('account_type', sa.String(), nullable=True))

    op.execute("UPDATE github_installations SET installation_id = github_installation_id")
    op.execute("UPDATE github_installations SET account_id = github_account_id")
    op.execute("UPDATE github_installations SET account_login = github_account_login")
    op.execute("UPDATE github_installations SET account_type = github_account_type")

    op.alter_column('github_installations', 'installation_id', nullable=False)
    op.create_index('ix_github_installations_installation_id', 'github_installations', ['installation_id'], unique=True)

    op.drop_column('github_installations', 'status')
    op.drop_column('github_installations', 'uninstalled_at')
    op.drop_column('github_installations', 'installed_at')
    op.drop_column('github_installations', 'github_account_type')
    op.drop_column('github_installations', 'github_account_login')
    op.drop_column('github_installations', 'github_account_id')
    op.drop_column('github_installations', 'github_installation_id')
    op.drop_column('github_installations', 'organization_id')

    op.create_foreign_key('github_installations_user_id_fkey', 'github_installations', 'users', ['user_id'], ['id'], ondelete='CASCADE')

    # Drop organization_members and organizations
    op.drop_table('organization_members')
    op.drop_table('organizations')
