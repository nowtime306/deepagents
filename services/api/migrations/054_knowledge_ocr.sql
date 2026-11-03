ALTER TABLE conversations DROP CONSTRAINT conversations_internal_purpose_check;
ALTER TABLE conversations ADD CONSTRAINT conversations_internal_purpose_check CHECK(internal_purpose IN ('memory','ocr'));
CREATE TABLE knowledge_ocr_jobs (
    id uuid PRIMARY KEY,
    owner_id uuid NOT NULL REFERENCES users(id),
    knowledge_id uuid NOT NULL REFERENCES knowledge_bases(id) ON DELETE CASCADE,
    source_name text NOT NULL,
    source_digest text NOT NULL,
    page_start integer NOT NULL,
    page_end integer NOT NULL,
    model_id text NOT NULL,
    model_revision integer NOT NULL,
    internal_conversation_id uuid REFERENCES conversations(id) ON DELETE SET NULL,
    run_id uuid REFERENCES task_runs(id) ON DELETE SET NULL,
    document_id uuid REFERENCES documents(id) ON DELETE SET NULL,
    status text NOT NULL DEFAULT 'PREPARING' CHECK(status IN ('PREPARING','PENDING','SUCCEEDED','IMPORTED','FAILED','UNKNOWN','CANCELLED')),
    draft jsonb,
    revision integer NOT NULL DEFAULT 1,
    error_code text,
    created_at timestamptz NOT NULL DEFAULT now(),
    finished_at timestamptz,
    UNIQUE(owner_id,knowledge_id,source_digest)
);
CREATE INDEX knowledge_ocr_owner ON knowledge_ocr_jobs(owner_id,created_at DESC);
