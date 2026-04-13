"""Add named understandings.

Revision ID: 004_v3
Revises: 003_v3
Create Date: 2026-04-08
"""

revision = "004_v3"
down_revision = "003_v3"
branch_labels = None
depends_on = None


def upgrade() -> None:
    from alembic import op

    op.execute(
        """
        CREATE TABLE named_understandings (
            workspace_id      INT NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
            name              TEXT NOT NULL,
            understanding_id  BIGINT NOT NULL REFERENCES understanding_records(id) ON DELETE CASCADE,
            created_at        TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            PRIMARY KEY (workspace_id, name)
        )
        """
    )
    op.execute(
        """
        CREATE INDEX ix_named_understandings_understanding_id
            ON named_understandings (understanding_id)
        """
    )
    op.execute(
        """
        INSERT INTO named_understandings (workspace_id, name, understanding_id)
        SELECT id, 'soul', soul_understanding_id
        FROM workspaces
        WHERE soul_understanding_id IS NOT NULL
        """
    )
    op.execute(
        """
        INSERT INTO named_understandings (workspace_id, name, understanding_id)
        SELECT id, 'protocol', protocol_understanding_id
        FROM workspaces
        WHERE protocol_understanding_id IS NOT NULL
        """
    )
    op.execute(
        """
        INSERT INTO named_understandings (workspace_id, name, understanding_id)
        SELECT id, 'orientation', orientation_understanding_id
        FROM workspaces
        WHERE orientation_understanding_id IS NOT NULL
        """
    )
    op.execute(
        """
        INSERT INTO named_understandings (workspace_id, name, understanding_id)
        SELECT id, 'consolidation', consolidation_understanding_id
        FROM workspaces
        WHERE consolidation_understanding_id IS NOT NULL
        """
    )


def downgrade() -> None:
    from alembic import op

    op.execute("DROP INDEX IF EXISTS ix_named_understandings_understanding_id")
    op.execute("DROP TABLE IF EXISTS named_understandings")
