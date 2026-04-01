"""Initial schema

Revision ID: 001
Revises:
Create Date: 2026-03-31
"""

revision = "001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    from alembic import op

    op.execute("CREATE EXTENSION IF NOT EXISTS vector")

    op.execute("""
        CREATE TABLE workspaces (
            id          SERIAL PRIMARY KEY,
            name        TEXT NOT NULL UNIQUE,
            created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
    """)

    # nodes — core entity table
    op.execute("""
        CREATE TABLE nodes (
            id                  BIGSERIAL PRIMARY KEY,
            workspace_id        INT NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
            name                TEXT NOT NULL,
            entity_type         TEXT NOT NULL,
            summary             TEXT,
            summary_updated_at  TIMESTAMPTZ,
            tags                TEXT[] NOT NULL DEFAULT '{}',
            created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            updated_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            CONSTRAINT uq_nodes_workspace_name UNIQUE (workspace_id, name)
        )
    """)

    op.execute("CREATE INDEX idx_nodes_workspace_type ON nodes (workspace_id, entity_type)")
    op.execute("CREATE INDEX idx_nodes_updated_at ON nodes (updated_at DESC)")
    op.execute("CREATE INDEX idx_nodes_tags ON nodes USING GIN (tags)")

    # observations — ordered facts attached to a node
    op.execute("""
        CREATE TABLE observations (
            id          BIGSERIAL PRIMARY KEY,
            node_id     BIGINT NOT NULL REFERENCES nodes(id) ON DELETE CASCADE,
            ordinal     INT NOT NULL,
            content     TEXT NOT NULL,
            -- tsvector for FTS (text search mode)
            content_tsv TSVECTOR GENERATED ALWAYS AS (to_tsvector('english', content)) STORED,
            created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            CONSTRAINT uq_observations_node_ordinal UNIQUE (node_id, ordinal)
        )
    """)

    op.execute("CREATE INDEX idx_observations_node_ordinal ON observations (node_id, ordinal)")
    op.execute("CREATE INDEX idx_observations_tsv ON observations USING GIN (content_tsv)")

    # relations — directed typed edges
    op.execute("""
        CREATE TABLE relations (
            id              BIGSERIAL PRIMARY KEY,
            workspace_id    INT NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
            from_node_id    BIGINT NOT NULL REFERENCES nodes(id) ON DELETE CASCADE,
            to_node_id      BIGINT NOT NULL REFERENCES nodes(id) ON DELETE CASCADE,
            relation_type   TEXT NOT NULL,
            created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            CONSTRAINT uq_relations UNIQUE (workspace_id, from_node_id, to_node_id, relation_type)
        )
    """)

    op.execute("CREATE INDEX idx_relations_from ON relations (from_node_id)")
    op.execute("CREATE INDEX idx_relations_to ON relations (to_node_id)")

    # perspectives — embedding angles (configurable per workspace)
    op.execute("""
        CREATE TABLE perspectives (
            id              SERIAL PRIMARY KEY,
            workspace_id    INT REFERENCES workspaces(id) ON DELETE CASCADE,
            name            TEXT NOT NULL,
            instruction     TEXT NOT NULL,   -- prefix injected before embedding
            CONSTRAINT uq_perspectives UNIQUE (workspace_id, name)
        )
    """)

    # NULL workspace_id uniqueness for perspectives
    op.execute("""
        CREATE UNIQUE INDEX uq_perspectives_null_workspace
            ON perspectives (name)
            WHERE workspace_id IS NULL
    """)

    # Seed default perspectives (workspace_id NULL = shared default)
    op.execute("""
        INSERT INTO perspectives (workspace_id, name, instruction) VALUES
            (NULL, 'general',    'Represent the following memory fact for general retrieval:'),
            (NULL, 'technical',  'Represent the following memory fact focusing on technical details, systems, and implementation:'),
            (NULL, 'relational', 'Represent the following memory fact focusing on people, relationships, and social context:'),
            (NULL, 'temporal',   'Represent the following memory fact focusing on time, sequence, and change over time:'),
            (NULL, 'project',    'Represent the following memory fact focusing on project goals, decisions, and status:')
    """)

    # embeddings — per-observation, per-perspective vectors
    # Dimension 768 matches nomic-embed-text. Will need migration if model changes.
    op.execute("""
        CREATE TABLE embeddings (
            id              BIGSERIAL PRIMARY KEY,
            observation_id  BIGINT NOT NULL REFERENCES observations(id) ON DELETE CASCADE,
            perspective_id  INT NOT NULL REFERENCES perspectives(id) ON DELETE CASCADE,
            vector          VECTOR(768) NOT NULL,
            embedded_at     TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            CONSTRAINT uq_embeddings UNIQUE (observation_id, perspective_id)
        )
    """)

    op.execute("""
        CREATE INDEX idx_embeddings_vector
            ON embeddings USING hnsw (vector vector_cosine_ops)
    """)

    # node_embeddings — per-node aggregate (mean-pool across observations per perspective)
    # Maintained incrementally by the write pipeline.
    op.execute("""
        CREATE TABLE node_embeddings (
            id              BIGSERIAL PRIMARY KEY,
            node_id         BIGINT NOT NULL REFERENCES nodes(id) ON DELETE CASCADE,
            perspective_id  INT NOT NULL REFERENCES perspectives(id) ON DELETE CASCADE,
            vector          VECTOR(768) NOT NULL,
            updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            CONSTRAINT uq_node_embeddings UNIQUE (node_id, perspective_id)
        )
    """)

    op.execute("""
        CREATE INDEX idx_node_embeddings_vector
            ON node_embeddings USING hnsw (vector vector_cosine_ops)
    """)

    # events — audit log for consolidation reporting
    op.execute("""
        CREATE TABLE events (
            id          BIGSERIAL PRIMARY KEY,
            node_id     BIGINT REFERENCES nodes(id) ON DELETE SET NULL,
            workspace_id INT NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
            operation   TEXT NOT NULL,   -- 'create_node', 'add_observation', 'delete_node', etc.
            detail      JSONB,
            occurred_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
    """)

    op.execute("CREATE INDEX idx_events_workspace_time ON events (workspace_id, occurred_at DESC)")
    op.execute("CREATE INDEX idx_events_node ON events (node_id)")

    # Trigger: update nodes.updated_at whenever an observation is inserted/updated/deleted
    op.execute("""
        CREATE OR REPLACE FUNCTION touch_node_updated_at()
        RETURNS TRIGGER LANGUAGE plpgsql AS $$
        BEGIN
            UPDATE nodes SET updated_at = NOW()
            WHERE id = COALESCE(NEW.node_id, OLD.node_id);
            RETURN NEW;
        END;
        $$
    """)

    op.execute("""
        CREATE TRIGGER trg_observations_touch_node
            AFTER INSERT OR UPDATE OR DELETE ON observations
            FOR EACH ROW EXECUTE FUNCTION touch_node_updated_at()
    """)


def downgrade() -> None:
    from alembic import op

    op.execute("DROP TRIGGER IF EXISTS trg_observations_touch_node ON observations")
    op.execute("DROP FUNCTION IF EXISTS touch_node_updated_at")
    op.execute("DROP TABLE IF EXISTS events")
    op.execute("DROP TABLE IF EXISTS node_embeddings")
    op.execute("DROP TABLE IF EXISTS embeddings")
    op.execute("DROP TABLE IF EXISTS perspectives")
    op.execute("DROP TABLE IF EXISTS relations")
    op.execute("DROP TABLE IF EXISTS observations")
    op.execute("DROP TABLE IF EXISTS nodes")
    op.execute("DROP TABLE IF EXISTS workspaces")
    op.execute("DROP EXTENSION IF EXISTS vector")
