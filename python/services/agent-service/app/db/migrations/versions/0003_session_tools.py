"""sessions.tools and sessions.tool_calls_used: a session's tools-service tools, fixed when it starts,
and its tool calls, for the budget (docs/specs/agent-service/tools.md TL7, TL10)

Revision ID: 0003
Revises: 0002
Created: 2026-11-26, written by hand from app/db/models.py, then reviewed
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = '0003'
down_revision = '0002'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column('sessions', sa.Column('tools', postgresql.JSONB(astext_type=sa.Text()), nullable=True))
    op.add_column('sessions', sa.Column('tool_calls_used', sa.Integer(), server_default='0', nullable=False))


def downgrade() -> None:
    op.drop_column('sessions', 'tool_calls_used')
    op.drop_column('sessions', 'tools')
