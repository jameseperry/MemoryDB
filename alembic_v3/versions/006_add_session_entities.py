"""Add session entity support: started_at and session_understanding_id.

Revision ID: 006_v3
Revises: 005_v3
Create Date: 2026-04-26
"""

revision = "006_v3"
down_revision = "005_v3"
branch_labels = None
depends_on = None


def upgrade() -> None:
    from alembic import op

    op.execute(
        """
        ALTER TABLE sessions
        ADD COLUMN started_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        """
    )
    op.execute(
        """
        ALTER TABLE sessions
        ADD COLUMN session_understanding_id BIGINT
            REFERENCES understanding_records(id)
        """
    )

    # Backfill started_at from earliest record per session
    op.execute(
        """
        UPDATE sessions s
        SET started_at = COALESCE(
            (SELECT MIN(r.created_at) FROM records r WHERE r.session_id = s.session_id),
            s.updated_at
        )
        """
    )

    op.execute(
        "CREATE INDEX idx_v3_sessions_started_at ON sessions (workspace_id, started_at DESC)"
    )
    op.execute(
        "CREATE INDEX idx_v3_sessions_updated_at ON sessions (workspace_id, updated_at DESC)"
    )


def downgrade() -> None:
    from alembic import op

    op.execute("DROP INDEX IF EXISTS idx_v3_sessions_updated_at")
    op.execute("DROP INDEX IF EXISTS idx_v3_sessions_started_at")
    op.execute("ALTER TABLE sessions DROP COLUMN IF EXISTS session_understanding_id")
    op.execute("ALTER TABLE sessions DROP COLUMN IF EXISTS started_at")
