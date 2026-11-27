"""sessions.files: what a session may do at file-management, as granted at its start
(docs/specs/agent-service/files-and-mounts.md F6)

Revision ID: 0004
Revises: 0003
Created: 2026-11-27, written by hand from app/db/models.py, then reviewed
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = '0004'
down_revision = '0003'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column('sessions', sa.Column('files', postgresql.JSONB(astext_type=sa.Text()), nullable=True))


def downgrade() -> None:
    op.drop_column('sessions', 'files')
