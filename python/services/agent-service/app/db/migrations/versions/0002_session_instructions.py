"""sessions.instructions: the caller's instructions a session started with (control-plane.md C15)

Revision ID: 0002
Revises: 0001
Created: 2026-11-18, written by hand from app/db/models.py, then reviewed
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = '0002'
down_revision = '0001'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column('sessions', sa.Column('instructions', sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column('sessions', 'instructions')
