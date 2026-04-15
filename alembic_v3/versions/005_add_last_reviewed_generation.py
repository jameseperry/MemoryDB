"""Add last_reviewed_generation to subjects.

Revision ID: 005_v3
Revises: 004_v3
Create Date: 2026-04-14
"""

revision = "005_v3"
down_revision = "004_v3"
branch_labels = None
depends_on = None


def upgrade() -> None:
    from alembic import op

    op.execute(
        """
        ALTER TABLE subjects
        ADD COLUMN last_reviewed_generation INT
        """
    )


def downgrade() -> None:
    from alembic import op

    op.execute("ALTER TABLE subjects DROP COLUMN IF EXISTS last_reviewed_generation")
