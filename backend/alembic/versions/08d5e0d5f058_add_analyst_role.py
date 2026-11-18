"""add analyst role

A new role, "analyst": sees company-wide numbers (analytics), never one person's record.
The database only allows the roles in the user_valid_role check, so we replace that check.
(Autogenerate can't see changes to a check constraint, so this migration is written by hand.)

Revision ID: 08d5e0d5f058
Revises: d9fb1e89b584
Create Date: 2026-11-18 17:59:09.803030

"""
from typing import Sequence, Union

from alembic import op


# revision identifiers, used by Alembic.
revision: str = '08d5e0d5f058'
down_revision: Union[str, None] = 'd9fb1e89b584'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.drop_constraint('user_valid_role', 'users', type_='check')
    op.create_check_constraint('user_valid_role', 'users',
                               "role IN ('employee', 'manager', 'analyst', 'hr', 'admin')")


def downgrade() -> None:
    # Analysts become employees again first, or the old check would refuse their rows.
    op.execute("UPDATE users SET role = 'employee' WHERE role = 'analyst'")
    op.drop_constraint('user_valid_role', 'users', type_='check')
    op.create_check_constraint('user_valid_role', 'users', "role IN ('employee', 'manager', 'hr', 'admin')")
