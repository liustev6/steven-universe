"""the history: conversations, sessions, turns, events (control-plane.md "History")

Revision ID: 0001
Revises: (none)
Created: 2026-11-16, generated from app/db/models.py, then reviewed
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = '0001'
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table('conversations',
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('owner', sa.String(length=200), nullable=False),
    sa.Column('profile', sa.String(length=32), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('last_active_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_conversations'))
    )
    op.create_index(op.f('ix_conversations_owner'), 'conversations', ['owner'], unique=False)
    op.create_table('sessions',
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('conversation_id', sa.Uuid(), nullable=False),
    sa.Column('owner', sa.String(length=200), nullable=False),
    sa.Column('state', sa.Enum('starting', 'ready', 'busy', 'ended', name='session_state', native_enum=False, create_constraint=True, length=16), nullable=False),
    sa.Column('end_reason', sa.Enum('deleted', 'idle', 'wall_time', 'sandbox_lost', 'stopped', 'failed', name='end_reason', native_enum=False, create_constraint=True, length=16), nullable=True),
    sa.Column('sandbox_id', sa.Uuid(), nullable=True),
    sa.Column('capability_hash', sa.LargeBinary(length=32), nullable=True),
    sa.Column('next_seq', sa.BigInteger(), nullable=False),
    sa.Column('turns_used', sa.Integer(), nullable=False),
    sa.Column('started_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('last_activity_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('ended_at', sa.DateTime(timezone=True), nullable=True),
    sa.ForeignKeyConstraint(['conversation_id'], ['conversations.id'], name=op.f('fk_sessions_conversation_id_conversations')),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_sessions')),
    sa.UniqueConstraint('capability_hash', name=op.f('uq_sessions_capability_hash'))
    )
    op.create_index(op.f('ix_sessions_conversation_id'), 'sessions', ['conversation_id'], unique=False)
    op.create_index(op.f('ix_sessions_owner'), 'sessions', ['owner'], unique=False)
    op.create_index(op.f('ix_sessions_state'), 'sessions', ['state'], unique=False)
    op.create_index('uq_sessions_live_conversation', 'sessions', ['conversation_id'], unique=True, postgresql_where="state <> 'ended'")
    op.create_table('events',
    sa.Column('session_id', sa.Uuid(), nullable=False),
    sa.Column('seq', sa.BigInteger(), nullable=False),
    sa.Column('type', sa.String(length=32), nullable=False),
    sa.Column('turn_id', sa.Uuid(), nullable=True),
    sa.Column('source_seq', sa.BigInteger(), nullable=True),
    sa.Column('ts', sa.DateTime(timezone=True), nullable=False),
    sa.Column('payload', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
    sa.ForeignKeyConstraint(['session_id'], ['sessions.id'], name=op.f('fk_events_session_id_sessions')),
    sa.PrimaryKeyConstraint('session_id', 'seq', name=op.f('pk_events'))
    )
    op.create_index('uq_events_session_source_seq', 'events', ['session_id', 'source_seq'], unique=True, postgresql_where='source_seq IS NOT NULL')
    op.create_table('turns',
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('conversation_id', sa.Uuid(), nullable=False),
    sa.Column('session_id', sa.Uuid(), nullable=False),
    sa.Column('user_text', sa.Text(), nullable=False),
    sa.Column('reply_text', sa.Text(), nullable=True),
    sa.Column('status', sa.Enum('running', 'completed', 'interrupted', 'failed', name='turn_status', native_enum=False, create_constraint=True, length=16), nullable=False),
    sa.Column('reason', sa.String(length=200), nullable=True),
    sa.Column('first_text_ms', sa.Integer(), nullable=True),
    sa.Column('duration_ms', sa.Integer(), nullable=True),
    sa.Column('usage', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    sa.Column('started_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('completed_at', sa.DateTime(timezone=True), nullable=True),
    sa.ForeignKeyConstraint(['conversation_id'], ['conversations.id'], name=op.f('fk_turns_conversation_id_conversations')),
    sa.ForeignKeyConstraint(['session_id'], ['sessions.id'], name=op.f('fk_turns_session_id_sessions')),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_turns'))
    )
    op.create_index('ix_turns_conversation_started', 'turns', ['conversation_id', 'started_at'], unique=False)
    op.create_index(op.f('ix_turns_session_id'), 'turns', ['session_id'], unique=False)


def downgrade() -> None:
    op.drop_index(op.f('ix_turns_session_id'), table_name='turns')
    op.drop_index('ix_turns_conversation_started', table_name='turns')
    op.drop_table('turns')
    op.drop_index('uq_events_session_source_seq', table_name='events', postgresql_where='source_seq IS NOT NULL')
    op.drop_table('events')
    op.drop_index('uq_sessions_live_conversation', table_name='sessions', postgresql_where="state <> 'ended'")
    op.drop_index(op.f('ix_sessions_state'), table_name='sessions')
    op.drop_index(op.f('ix_sessions_owner'), table_name='sessions')
    op.drop_index(op.f('ix_sessions_conversation_id'), table_name='sessions')
    op.drop_table('sessions')
    op.drop_index(op.f('ix_conversations_owner'), table_name='conversations')
    op.drop_table('conversations')
