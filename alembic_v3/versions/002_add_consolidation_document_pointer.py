"""Add consolidation workspace document pointer.

Revision ID: 002_v3
Revises: 001_v3
Create Date: 2026-04-08
"""

revision = "002_v3"
down_revision = "001_v3"
branch_labels = None
depends_on = None


def upgrade() -> None:
    from alembic import op

    op.execute(
        """
        ALTER TABLE workspaces
            ADD COLUMN consolidation_understanding_id BIGINT,
            ADD CONSTRAINT fk_v3_workspaces_consolidation_understanding
                FOREIGN KEY (consolidation_understanding_id)
                REFERENCES understanding_records(id)
        """
    )


def downgrade() -> None:
    from alembic import op

    op.execute(
        """
        ALTER TABLE workspaces
            DROP CONSTRAINT IF EXISTS fk_v3_workspaces_consolidation_understanding,
            DROP COLUMN IF EXISTS consolidation_understanding_id
        """
    )
