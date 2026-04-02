"""Rename session_tokens to sessions and track session model tier

Revision ID: 003_v3
Revises: 002_v3
Create Date: 2026-04-02
"""

from alembic import op
import sqlalchemy as sa


revision = "003_v3"
down_revision = "002_v3"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.rename_table("session_tokens", "sessions")
    op.add_column("sessions", sa.Column("model_tier", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("sessions", "model_tier")
    op.rename_table("sessions", "session_tokens")
