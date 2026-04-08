"""Add lightweight observation-to-observation links.

Revision ID: 003_v3
Revises: 002_v3
Create Date: 2026-04-08
"""

revision = "003_v3"
down_revision = "002_v3"
branch_labels = None
depends_on = None


def upgrade() -> None:
    from alembic import op

    op.execute(
        """
        CREATE TABLE observation_links (
            source_observation_id BIGINT NOT NULL REFERENCES observation_records(id) ON DELETE CASCADE,
            target_observation_id BIGINT NOT NULL REFERENCES observation_records(id) ON DELETE CASCADE,
            PRIMARY KEY (source_observation_id, target_observation_id),
            CHECK (source_observation_id <> target_observation_id)
        )
        """
    )


def downgrade() -> None:
    from alembic import op

    op.execute("DROP TABLE IF EXISTS observation_links")
