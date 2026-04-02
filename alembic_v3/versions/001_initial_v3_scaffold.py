"""Initial v3 scaffold

Revision ID: 001_v3
Revises:
Create Date: 2026-04-02
"""

revision = "001_v3"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Create the minimal v3 workspace table and pgvector extension.

    The subject/understanding schema is intentionally deferred to follow-on
    revisions while the codebase scaffolding settles.
    """
    from alembic import op

    op.execute("CREATE EXTENSION IF NOT EXISTS vector")
    op.execute(
        """
        CREATE TABLE workspaces (
            id          SERIAL PRIMARY KEY,
            name        TEXT NOT NULL UNIQUE,
            created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
        """
    )


def downgrade() -> None:
    from alembic import op

    op.execute("DROP TABLE IF EXISTS workspaces")
    op.execute("DROP EXTENSION IF EXISTS vector")
