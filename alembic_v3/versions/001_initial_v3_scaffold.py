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
        CREATE TABLE sessions (
            session_id     BIGSERIAL PRIMARY KEY,
            workspace_id   INT NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
            session_token  TEXT NOT NULL,
            seen_set_token INT NOT NULL DEFAULT 0,
            updated_at     TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            model_tier     TEXT,
            UNIQUE (workspace_id, session_token)
        )
        """
    )

    op.execute(
        """
        CREATE TABLE records (
            id           BIGINT PRIMARY KEY DEFAULT nextval('global_id_seq'),
            workspace_id INT NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
            record_type  TEXT NOT NULL CHECK (record_type IN ('observation', 'understanding')),
            content      TEXT NOT NULL,
            confidence   DOUBLE PRECISION,
            generation   INT NOT NULL,
            session_id   BIGINT REFERENCES sessions(session_id) ON DELETE SET NULL,
            model_tier   TEXT,
            content_tsv  TSVECTOR GENERATED ALWAYS AS (to_tsvector('english', content)) STORED,
            created_at   TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            UNIQUE (id, workspace_id),
            UNIQUE (id, record_type),
            UNIQUE (id, workspace_id, record_type)
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
        CREATE TABLE understanding_records (
            id             BIGINT PRIMARY KEY,
            workspace_id   INT NOT NULL,
            record_type    TEXT NOT NULL DEFAULT 'understanding' CHECK (record_type = 'understanding'),
            summary        TEXT,
            kind           TEXT NOT NULL,
            reason         TEXT,
            superseded_by  BIGINT REFERENCES understanding_records(id),
            FOREIGN KEY (id, workspace_id, record_type)
                REFERENCES records(id, workspace_id, record_type)
                ON DELETE CASCADE
        )
        """
    )

    op.execute(
        """
        ALTER TABLE workspaces
            ADD CONSTRAINT fk_v3_workspaces_soul_understanding
                FOREIGN KEY (soul_understanding_id) REFERENCES understanding_records(id),
            ADD CONSTRAINT fk_v3_workspaces_protocol_understanding
                FOREIGN KEY (protocol_understanding_id) REFERENCES understanding_records(id),
            ADD CONSTRAINT fk_v3_workspaces_orientation_understanding
                FOREIGN KEY (orientation_understanding_id) REFERENCES understanding_records(id)
        """
    )

    op.execute(
        """
        ALTER TABLE subjects
            ADD CONSTRAINT fk_v3_subjects_single_subject_understanding
                FOREIGN KEY (single_subject_understanding_id) REFERENCES understanding_records(id),
            ADD CONSTRAINT fk_v3_subjects_structural_understanding
                FOREIGN KEY (structural_understanding_id) REFERENCES understanding_records(id)
        """
    )

    op.execute(
        """
        CREATE TABLE observation_records (
            id           BIGINT PRIMARY KEY,
            workspace_id INT NOT NULL,
            record_type  TEXT NOT NULL DEFAULT 'observation' CHECK (record_type = 'observation'),
            content_hash TEXT NOT NULL,
            kind         TEXT,
            UNIQUE (workspace_id, content_hash),
            FOREIGN KEY (id, workspace_id, record_type)
                REFERENCES records(id, workspace_id, record_type)
                ON DELETE CASCADE
        )
        """
    )

    op.execute(
        """
        CREATE TABLE observation_subjects (
            observation_id BIGINT NOT NULL REFERENCES observation_records(id) ON DELETE CASCADE,
            subject_id     BIGINT NOT NULL REFERENCES subjects(id) ON DELETE CASCADE,
            PRIMARY KEY (observation_id, subject_id)
        )
        """
    )

    op.execute(
        """
        CREATE TABLE understanding_subjects (
            understanding_id BIGINT NOT NULL REFERENCES understanding_records(id) ON DELETE CASCADE,
            subject_id       BIGINT NOT NULL REFERENCES subjects(id) ON DELETE CASCADE,
            PRIMARY KEY (understanding_id, subject_id)
        )
        """
    )

    op.execute(
        """
        CREATE TABLE understanding_sources (
            understanding_id BIGINT NOT NULL REFERENCES understanding_records(id) ON DELETE CASCADE,
            observation_id   BIGINT NOT NULL REFERENCES observation_records(id) ON DELETE CASCADE,
            PRIMARY KEY (understanding_id, observation_id)
        )
        """
    )

    op.execute(
        """
        CREATE TABLE utility_signals (
            id           BIGSERIAL PRIMARY KEY,
            workspace_id INT NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
            target_id    BIGINT NOT NULL,
            signal_type  TEXT NOT NULL,
            reason       TEXT,
            session_id   BIGINT REFERENCES sessions(session_id) ON DELETE SET NULL,
            created_at   TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            FOREIGN KEY (target_id, workspace_id)
                REFERENCES records(id, workspace_id)
                ON DELETE CASCADE
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
            workspace_id   INT NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
            target_id      BIGINT NOT NULL,
            perspective_id BIGINT NOT NULL REFERENCES perspectives(id) ON DELETE CASCADE,
            vector         VECTOR(768) NOT NULL,
            model_version  TEXT NOT NULL,
            created_at     TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            UNIQUE (workspace_id, target_id, perspective_id),
            FOREIGN KEY (target_id, workspace_id)
                REFERENCES records(id, workspace_id)
                ON DELETE CASCADE
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
            session_id   BIGINT REFERENCES sessions(session_id) ON DELETE SET NULL,
            timestamp    TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            operation    TEXT NOT NULL,
            detail       JSONB
        )
        """
    )

    op.execute(
        """
        CREATE TABLE surfaced_in_session (
            session_id  BIGINT NOT NULL REFERENCES sessions(session_id) ON DELETE CASCADE,
            id          BIGINT NOT NULL,
            surfaced_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            PRIMARY KEY (session_id, id)
        )
        """
    )

    op.execute(
        """
        CREATE VIEW observations AS
        SELECT
            r.id,
            r.workspace_id,
            r.content,
            o.content_hash,
            o.kind,
            r.confidence,
            r.generation,
            r.session_id,
            r.model_tier,
            r.content_tsv,
            r.created_at
        FROM records r
        JOIN observation_records o ON o.id = r.id
        WHERE r.record_type = 'observation'
        """
    )

    op.execute(
        """
        CREATE VIEW understandings AS
        SELECT
            r.id,
            r.workspace_id,
            r.content,
            u.summary,
            u.kind,
            r.confidence,
            r.generation,
            r.session_id,
            r.model_tier,
            u.reason,
            r.content_tsv,
            r.created_at,
            u.superseded_by
        FROM records r
        JOIN understanding_records u ON u.id = r.id
        WHERE r.record_type = 'understanding'
        """
    )

    op.execute(
        """
        CREATE OR REPLACE FUNCTION write_observations_view()
        RETURNS TRIGGER LANGUAGE plpgsql AS $$
        DECLARE
            record_row observations;
            record_id BIGINT;
        BEGIN
            IF TG_OP = 'INSERT' THEN
                INSERT INTO records (
                    id,
                    workspace_id,
                    record_type,
                    content,
                    confidence,
                    generation,
                    session_id,
                    model_tier,
                    created_at
                )
                VALUES (
                    COALESCE(NEW.id, nextval('global_id_seq')),
                    NEW.workspace_id,
                    'observation',
                    NEW.content,
                    NEW.confidence,
                    NEW.generation,
                    NEW.session_id,
                    NEW.model_tier,
                    COALESCE(NEW.created_at, NOW())
                )
                RETURNING id INTO record_id;

                INSERT INTO observation_records (
                    id,
                    workspace_id,
                    content_hash,
                    kind
                )
                VALUES (
                    record_id,
                    NEW.workspace_id,
                    NEW.content_hash,
                    NEW.kind
                );

                SELECT * INTO record_row FROM observations WHERE id = record_id;
                RETURN record_row;
            ELSIF TG_OP = 'UPDATE' THEN
                UPDATE records
                SET
                    content = NEW.content,
                    confidence = NEW.confidence,
                    generation = NEW.generation,
                    session_id = NEW.session_id,
                    model_tier = NEW.model_tier,
                    created_at = NEW.created_at
                WHERE id = OLD.id;

                UPDATE observation_records
                SET
                    content_hash = NEW.content_hash,
                    kind = NEW.kind
                WHERE id = OLD.id;

                SELECT * INTO record_row FROM observations WHERE id = OLD.id;
                RETURN record_row;
            ELSIF TG_OP = 'DELETE' THEN
                DELETE FROM records WHERE id = OLD.id;
                RETURN OLD;
            END IF;
            RETURN NULL;
        END;
        $$;
        """
    )

    op.execute(
        """
        CREATE OR REPLACE FUNCTION write_understandings_view()
        RETURNS TRIGGER LANGUAGE plpgsql AS $$
        DECLARE
            record_row understandings;
            record_id BIGINT;
        BEGIN
            IF TG_OP = 'INSERT' THEN
                INSERT INTO records (
                    id,
                    workspace_id,
                    record_type,
                    content,
                    confidence,
                    generation,
                    session_id,
                    model_tier,
                    created_at
                )
                VALUES (
                    COALESCE(NEW.id, nextval('global_id_seq')),
                    NEW.workspace_id,
                    'understanding',
                    NEW.content,
                    NEW.confidence,
                    NEW.generation,
                    NEW.session_id,
                    NEW.model_tier,
                    COALESCE(NEW.created_at, NOW())
                )
                RETURNING id INTO record_id;

                INSERT INTO understanding_records (
                    id,
                    workspace_id,
                    summary,
                    kind,
                    reason,
                    superseded_by
                )
                VALUES (
                    record_id,
                    NEW.workspace_id,
                    NEW.summary,
                    NEW.kind,
                    NEW.reason,
                    NEW.superseded_by
                );

                SELECT * INTO record_row FROM understandings WHERE id = record_id;
                RETURN record_row;
            ELSIF TG_OP = 'UPDATE' THEN
                UPDATE records
                SET
                    content = NEW.content,
                    confidence = NEW.confidence,
                    generation = NEW.generation,
                    session_id = NEW.session_id,
                    model_tier = NEW.model_tier,
                    created_at = NEW.created_at
                WHERE id = OLD.id;

                UPDATE understanding_records
                SET
                    summary = NEW.summary,
                    kind = NEW.kind,
                    reason = NEW.reason,
                    superseded_by = NEW.superseded_by
                WHERE id = OLD.id;

                SELECT * INTO record_row FROM understandings WHERE id = OLD.id;
                RETURN record_row;
            ELSIF TG_OP = 'DELETE' THEN
                DELETE FROM records WHERE id = OLD.id;
                RETURN OLD;
            END IF;
            RETURN NULL;
        END;
        $$;
        """
    )

    op.execute(
        """
        CREATE TRIGGER trg_write_observations_view
            INSTEAD OF INSERT OR UPDATE OR DELETE ON observations
            FOR EACH ROW EXECUTE FUNCTION write_observations_view()
        """
    )

    op.execute(
        """
        CREATE TRIGGER trg_write_understandings_view
            INSTEAD OF INSERT OR UPDATE OR DELETE ON understandings
            FOR EACH ROW EXECUTE FUNCTION write_understandings_view()
        """
    )

    op.execute("CREATE INDEX idx_v3_subject_tags ON subjects USING GIN (tags)")
    op.execute("CREATE INDEX idx_v3_records_tsv ON records USING GIN (content_tsv)")
    op.execute("CREATE INDEX idx_v3_events_workspace_time ON events (workspace_id, timestamp DESC)")
    op.execute("CREATE INDEX idx_v3_records_workspace_created ON records (workspace_id, created_at DESC)")
    op.execute("CREATE INDEX idx_v3_records_workspace_type_created ON records (workspace_id, record_type, created_at DESC)")
    op.execute("CREATE INDEX idx_v3_understanding_records_workspace_kind ON understanding_records (workspace_id, kind, superseded_by)")
    op.execute("CREATE INDEX idx_v3_sessions_workspace_token ON sessions (workspace_id, session_token)")
    op.execute("CREATE INDEX idx_v3_surfaced_session_time ON surfaced_in_session (session_id, surfaced_at DESC)")
    op.execute("CREATE INDEX idx_v3_utility_workspace_created ON utility_signals (workspace_id, created_at DESC)")
    op.execute("CREATE INDEX idx_v3_embeddings_workspace_target ON embeddings (workspace_id, target_id)")


def downgrade() -> None:
    from alembic import op

    op.execute("DROP TRIGGER IF EXISTS trg_write_understandings_view ON understandings")
    op.execute("DROP TRIGGER IF EXISTS trg_write_observations_view ON observations")
    op.execute("DROP FUNCTION IF EXISTS write_understandings_view")
    op.execute("DROP FUNCTION IF EXISTS write_observations_view")
    op.execute("DROP VIEW IF EXISTS understandings")
    op.execute("DROP VIEW IF EXISTS observations")
    op.execute("DROP TABLE IF EXISTS surfaced_in_session")
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
    op.execute("DROP TABLE IF EXISTS observation_records")
    op.execute("DROP TABLE IF EXISTS understanding_records")
    op.execute("DROP TABLE IF EXISTS records")
    op.execute("DROP TABLE IF EXISTS subjects")
    op.execute("DROP TABLE IF EXISTS sessions")
    op.execute("DROP TABLE IF EXISTS workspaces")
    op.execute("DROP SEQUENCE IF EXISTS global_id_seq")
    op.execute("DROP EXTENSION IF EXISTS vector")
