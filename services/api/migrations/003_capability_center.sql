ALTER TABLE model_configs ADD COLUMN kind text NOT NULL DEFAULT 'chat' CHECK(kind IN ('chat','embedding'));
CREATE TABLE knowledge_bases (
    id uuid PRIMARY KEY, owner_id uuid NOT NULL REFERENCES users(id), name text NOT NULL,
    description text NOT NULL DEFAULT '', embedding_model_id text,
    created_at timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE documents (
    id uuid PRIMARY KEY, knowledge_id uuid NOT NULL REFERENCES knowledge_bases(id) ON DELETE CASCADE,
    name text NOT NULL, size integer NOT NULL, status text NOT NULL, error text,
    vector_status text NOT NULL DEFAULT 'none', created_at timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE document_chunks (
    id uuid PRIMARY KEY, document_id uuid NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
    position integer NOT NULL, location text NOT NULL, content text NOT NULL
);
CREATE INDEX chunks_document ON document_chunks(document_id,position);
CREATE TABLE skills (
    id uuid PRIMARY KEY, owner_id uuid NOT NULL REFERENCES users(id), name text NOT NULL,
    description text NOT NULL, files jsonb NOT NULL, version integer NOT NULL DEFAULT 1,
    enabled boolean NOT NULL DEFAULT true, created_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE(owner_id,name)
);
CREATE TABLE mcp_servers (
    id uuid PRIMARY KEY, name text NOT NULL UNIQUE, url text NOT NULL,
    token_ciphertext text NOT NULL DEFAULT '', enabled boolean NOT NULL DEFAULT true,
    allow_private boolean NOT NULL DEFAULT false, status text NOT NULL DEFAULT 'not_tested',
    tools jsonb NOT NULL DEFAULT '[]', created_at timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE capability_bindings (
    owner_id uuid PRIMARY KEY REFERENCES users(id), knowledge_ids jsonb NOT NULL DEFAULT '[]',
    skill_ids jsonb NOT NULL DEFAULT '[]', mcp_ids jsonb NOT NULL DEFAULT '[]',
    updated_at timestamptz NOT NULL DEFAULT now()
);
