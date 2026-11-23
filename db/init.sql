-- platform-copilot schema. Idempotent: safe to run on an empty database.
CREATE EXTENSION IF NOT EXISTS vector;

CREATE TABLE users (
    id     text PRIMARY KEY,
    name   text NOT NULL,
    groups text[] NOT NULL
);

-- RAG: one row per markdown file, one row per chunk.
CREATE TABLE documents (
    id      serial PRIMARY KEY,
    path    text NOT NULL UNIQUE,
    title   text NOT NULL,
    "group" text NOT NULL
);

CREATE TABLE chunks (
    id          serial PRIMARY KEY,
    document_id integer NOT NULL REFERENCES documents ON DELETE CASCADE,
    source_id   text NOT NULL UNIQUE,          -- e.g. access-policy.md#datos-con-pii
    heading     text NOT NULL,                 -- "Política de acceso > Datos con PII"
    content     text NOT NULL,
    "group"     text NOT NULL,                 -- copied from the document: filter without a join
    embedding   vector(768) NOT NULL,
    tsv         tsvector GENERATED ALWAYS AS (to_tsvector('spanish', heading || ' ' || content)) STORED
);
CREATE INDEX chunks_embedding_idx ON chunks USING hnsw (embedding vector_cosine_ops);
CREATE INDEX chunks_tsv_idx ON chunks USING gin (tsv);
CREATE INDEX chunks_group_idx ON chunks ("group");

-- The data catalog the agent can query (read-only).
CREATE TABLE data_products (
    id          serial PRIMARY KEY,
    name        text NOT NULL UNIQUE,
    domain      text NOT NULL,
    country     text NOT NULL,
    owner       text NOT NULL REFERENCES users,
    description text NOT NULL,
    pii         boolean NOT NULL
);

CREATE TABLE access_requests (
    id         serial PRIMARY KEY,
    product    text NOT NULL REFERENCES data_products (name),
    requester  text NOT NULL REFERENCES users,
    reason     text NOT NULL,
    status     text NOT NULL DEFAULT 'pending' CHECK (status IN ('pending', 'approved', 'rejected')),
    created_at timestamptz NOT NULL DEFAULT now(),
    decided_by text REFERENCES users,
    decided_at timestamptz
);

-- Append-only audit log: the database refuses UPDATE and DELETE, whoever asks.
CREATE TABLE audit_log (
    id              bigserial PRIMARY KEY,
    ts              timestamptz NOT NULL DEFAULT now(),
    user_id         text NOT NULL,
    conversation_id text NOT NULL,
    kind            text NOT NULL,
    payload         jsonb NOT NULL
);

CREATE FUNCTION audit_log_append_only() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION 'audit_log is append-only';
END $$;

CREATE TRIGGER audit_log_no_change BEFORE UPDATE OR DELETE ON audit_log
    FOR EACH ROW EXECUTE FUNCTION audit_log_append_only();

-- Role used only by the query_catalog tool: it can read data_products and nothing else.
-- NOLOGIN: the API switches to it with SET ROLE inside a read-only transaction.
DO $$
BEGIN
    IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'catalog_ro') THEN
        CREATE ROLE catalog_ro NOLOGIN;
    END IF;
END $$;
GRANT USAGE ON SCHEMA public TO catalog_ro;
GRANT SELECT ON data_products TO catalog_ro;
GRANT catalog_ro TO CURRENT_USER;
