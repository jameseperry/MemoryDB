"""Initial v3 schema

Revision ID: 001_v3
Revises:
Create Date: 2026-04-02
"""

revision = "001_v3"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Create the current v3 workspace/subject/understanding schema."""
    from alembic import op

    op.execute("CREATE EXTENSION IF NOT EXISTS vector")
    op.execute("CREATE SEQUENCE IF NOT EXISTS global_id_seq")

    op.execute(
        """
        CREATE TABLE id_registry (
            id   BIGINT PRIMARY KEY,
            kind TEXT NOT NULL CHECK (kind IN ('observation', 'understanding'))
        )
        """
    )

    op.execute(
        """
        CREATE TABLE workspaces (
            id                        SERIAL PRIMARY KEY,
            name                      TEXT NOT NULL UNIQUE,
            created_at                TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            soul_understanding_id     BIGINT,
            protocol_understanding_id BIGINT,
            orientation_understanding_id BIGINT,
            current_generation        INT NOT NULL DEFAULT 0,
            last_consolidated_at      TIMESTAMPTZ
        )
        """
    )

    op.execute(
        """
        CREATE TABLE subjects (
            id                              BIGSERIAL PRIMARY KEY,
            workspace_id                    INT NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
            name                            TEXT NOT NULL,
            summary                         TEXT,
            tags                            TEXT[] NOT NULL DEFAULT '{}',
            created_at                      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            single_subject_understanding_id BIGINT,
            structural_understanding_id     BIGINT,
            UNIQUE (workspace_id, name)
        )
        """
    )

    op.execute(
        """
        CREATE TABLE understandings (
            id             BIGINT PRIMARY KEY DEFAULT nextval('global_id_seq'),
            workspace_id   INT NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
            content        TEXT NOT NULL,
            summary        TEXT,
            kind           TEXT NOT NULL,
            generation     INT NOT NULL,
            session_id     TEXT,
            model_tier     TEXT,
            reason         TEXT,
            content_tsv    TSVECTOR GENERATED ALWAYS AS (to_tsvector('english', content)) STORED,
            created_at     TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            superseded_by  BIGINT REFERENCES understandings(id)
        )
        """
    )

    op.execute(
        """
        ALTER TABLE workspaces
            ADD CONSTRAINT fk_v3_workspaces_soul_understanding
                FOREIGN KEY (soul_understanding_id) REFERENCES understandings(id),
            ADD CONSTRAINT fk_v3_workspaces_protocol_understanding
                FOREIGN KEY (protocol_understanding_id) REFERENCES understandings(id),
            ADD CONSTRAINT fk_v3_workspaces_orientation_understanding
                FOREIGN KEY (orientation_understanding_id) REFERENCES understandings(id)
        """
    )

    op.execute(
        """
        ALTER TABLE subjects
            ADD CONSTRAINT fk_v3_subjects_single_subject_understanding
                FOREIGN KEY (single_subject_understanding_id) REFERENCES understandings(id),
            ADD CONSTRAINT fk_v3_subjects_structural_understanding
                FOREIGN KEY (structural_understanding_id) REFERENCES understandings(id)
        """
    )

    op.execute(
        """
        CREATE TABLE observations (
            id           BIGINT PRIMARY KEY DEFAULT nextval('global_id_seq'),
            workspace_id INT NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
            content      TEXT NOT NULL,
            content_hash TEXT NOT NULL,
            kind         TEXT,
            confidence   DOUBLE PRECISION,
            generation   INT NOT NULL,
            observed_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            session_id   TEXT,
            model_tier   TEXT,
            content_tsv  TSVECTOR GENERATED ALWAYS AS (to_tsvector('english', content)) STORED,
            created_at   TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            UNIQUE (workspace_id, content_hash)
        )
        """
    )

    op.execute(
        """
        CREATE TABLE observation_subjects (
            observation_id BIGINT NOT NULL REFERENCES observations(id) ON DELETE CASCADE,
            subject_id     BIGINT NOT NULL REFERENCES subjects(id) ON DELETE CASCADE,
            PRIMARY KEY (observation_id, subject_id)
        )
        """
    )

    op.execute(
        """
        CREATE TABLE understanding_subjects (
            understanding_id BIGINT NOT NULL REFERENCES understandings(id) ON DELETE CASCADE,
            subject_id       BIGINT NOT NULL REFERENCES subjects(id) ON DELETE CASCADE,
            PRIMARY KEY (understanding_id, subject_id)
        )
        """
    )

    op.execute(
        """
        CREATE TABLE understanding_sources (
            understanding_id BIGINT NOT NULL REFERENCES understandings(id) ON DELETE CASCADE,
            observation_id   BIGINT NOT NULL REFERENCES observations(id) ON DELETE CASCADE,
            PRIMARY KEY (understanding_id, observation_id)
        )
        """
    )

    op.execute(
        """
        CREATE TABLE utility_signals (
            id          BIGSERIAL PRIMARY KEY,
            target_id   BIGINT NOT NULL,
            signal_type TEXT NOT NULL,
            reason      TEXT,
            session_id  TEXT,
            created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
        """
    )

    op.execute(
        """
        CREATE TABLE perspectives (
            id           BIGSERIAL PRIMARY KEY,
            workspace_id INT REFERENCES workspaces(id) ON DELETE CASCADE,
            name         TEXT NOT NULL,
            instruction  TEXT NOT NULL,
            is_default   BOOLEAN NOT NULL DEFAULT FALSE,
            UNIQUE (workspace_id, name)
        )
        """
    )

    op.execute(
        """
        CREATE UNIQUE INDEX uq_perspectives_null_workspace_v3
            ON perspectives (name)
            WHERE workspace_id IS NULL
        """
    )

    op.execute(
        """
        INSERT INTO perspectives (workspace_id, name, instruction, is_default) VALUES
            (NULL, 'general',    'Represent for retrieval with broad semantic coverage:', true),
            (NULL, 'technical',  'Represent for retrieval about technical design and implementation:', true),
            (NULL, 'relational', 'Represent for retrieval about relationships, collaboration, and personal context:', true),
            (NULL, 'temporal',   'Represent for retrieval about decisions made, changes over time, and open questions:', true),
            (NULL, 'project',    'Represent for retrieval about project state and progress:', true)
        """
    )

    op.execute(
        """
        CREATE TABLE embeddings (
            id             BIGSERIAL PRIMARY KEY,
            target_id      BIGINT NOT NULL,
            perspective_id BIGINT NOT NULL REFERENCES perspectives(id) ON DELETE CASCADE,
            vector         VECTOR(768) NOT NULL,
            model_version  TEXT NOT NULL,
            created_at     TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            UNIQUE (target_id, perspective_id)
        )
        """
    )

    op.execute(
        """
        CREATE INDEX idx_v3_embeddings_vector
            ON embeddings USING hnsw (vector vector_cosine_ops)
        """
    )

    op.execute(
        """
        CREATE TABLE events (
            id           BIGSERIAL PRIMARY KEY,
            workspace_id INT NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
            session_id   TEXT,
            timestamp    TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            operation    TEXT NOT NULL,
            detail       JSONB
        )
        """
    )

    op.execute(
        """
        CREATE TABLE sessions (
            session_id    TEXT PRIMARY KEY,
            current_token INT NOT NULL,
            updated_at    TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            model_tier    TEXT
        )
        """
    )

    op.execute(
        """
        CREATE TABLE surfaced_in_session (
            session_id  TEXT NOT NULL,
            id          BIGINT NOT NULL,
            surfaced_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            PRIMARY KEY (session_id, id)
        )
        """
    )

    op.execute("CREATE INDEX idx_v3_subject_tags ON subjects USING GIN (tags)")
    op.execute("CREATE INDEX idx_v3_obs_tsv ON observations USING GIN (content_tsv)")
    op.execute("CREATE INDEX idx_v3_und_tsv ON understandings USING GIN (content_tsv)")
    op.execute("CREATE INDEX idx_v3_events_workspace_time ON events (workspace_id, timestamp DESC)")
    op.execute("CREATE INDEX idx_v3_obs_workspace_created ON observations (workspace_id, created_at DESC)")
    op.execute("CREATE INDEX idx_v3_und_workspace_created ON understandings (workspace_id, created_at DESC)")

    op.execute(
        """
        CREATE OR REPLACE FUNCTION register_global_id_kind()
        RETURNS TRIGGER LANGUAGE plpgsql AS $$
        BEGIN
            INSERT INTO id_registry (id, kind) VALUES (NEW.id, TG_ARGV[0]);
            RETURN NEW;
        END;
        $$;
        """
    )

    for table_name, kind in [
        ("observations", "observation"),
        ("understandings", "understanding"),
    ]:
        op.execute(
            f"""
            CREATE TRIGGER trg_register_{table_name}_id
                AFTER INSERT ON {table_name}
                FOR EACH ROW EXECUTE FUNCTION register_global_id_kind('{kind}')
            """
        )


def downgrade() -> None:
    from alembic import op

    for table_name in ["understandings", "observations"]:
        op.execute(f"DROP TRIGGER IF EXISTS trg_register_{table_name}_id ON {table_name}")

    op.execute("DROP FUNCTION IF EXISTS register_global_id_kind")
    op.execute("DROP TABLE IF EXISTS surfaced_in_session")
    op.execute("DROP TABLE IF EXISTS sessions")
    op.execute("DROP TABLE IF EXISTS events")
    op.execute("DROP TABLE IF EXISTS embeddings")
    op.execute("DROP TABLE IF EXISTS perspectives")
    op.execute("DROP TABLE IF EXISTS utility_signals")
    op.execute("DROP TABLE IF EXISTS understanding_sources")
    op.execute("DROP TABLE IF EXISTS understanding_subjects")
    op.execute("DROP TABLE IF EXISTS observation_subjects")
    op.execute("DROP TABLE IF EXISTS observations")
    op.execute(
        """
        ALTER TABLE subjects
            DROP CONSTRAINT IF EXISTS fk_v3_subjects_single_subject_understanding,
            DROP CONSTRAINT IF EXISTS fk_v3_subjects_structural_understanding
        """
    )
    op.execute(
        """
        ALTER TABLE workspaces
            DROP CONSTRAINT IF EXISTS fk_v3_workspaces_soul_understanding,
            DROP CONSTRAINT IF EXISTS fk_v3_workspaces_protocol_understanding,
            DROP CONSTRAINT IF EXISTS fk_v3_workspaces_orientation_understanding
        """
    )
    op.execute("DROP TABLE IF EXISTS understandings")
    op.execute("DROP TABLE IF EXISTS subjects")
    op.execute("DROP TABLE IF EXISTS id_registry")
    op.execute("DROP TABLE IF EXISTS workspaces")
    op.execute("DROP SEQUENCE IF EXISTS global_id_seq")
    op.execute("DROP EXTENSION IF EXISTS vector")
