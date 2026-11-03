ALTER TABLE conversations ADD COLUMN internal_purpose text CHECK(internal_purpose IN ('memory'));
ALTER TABLE private_memories ADD COLUMN scope_kind text NOT NULL DEFAULT 'global'
    CHECK(scope_kind IN ('global','project','agent'));
ALTER TABLE private_memories ADD COLUMN scope_id uuid;
ALTER TABLE private_memories ADD COLUMN expires_at timestamptz;
ALTER TABLE private_memories ADD CONSTRAINT memory_scope_valid CHECK((scope_kind='global')=(scope_id IS NULL));
ALTER TABLE private_memories ADD COLUMN content_digest text NOT NULL DEFAULT '';
ALTER TABLE private_memories ADD COLUMN origin text NOT NULL DEFAULT 'manual' CHECK(origin IN ('manual','candidate'));
UPDATE private_memories SET content_digest=encode(sha256(convert_to(content,'UTF8')),'hex');

CREATE TABLE memory_jobs (
    id uuid PRIMARY KEY,
    owner_id uuid NOT NULL REFERENCES users(id),
    source_conversation_id uuid NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
    internal_conversation_id uuid REFERENCES conversations(id) ON DELETE SET NULL,
    run_id uuid REFERENCES task_runs(id) ON DELETE SET NULL,
    source_message_ids jsonb NOT NULL,
    source_digest text NOT NULL,
    model_id text NOT NULL,
    model_revision integer NOT NULL,
    status text NOT NULL DEFAULT 'PENDING' CHECK(status IN ('PENDING','SUCCEEDED','FAILED','UNKNOWN','CANCELLED')),
    error_code text,
    created_at timestamptz NOT NULL DEFAULT now(),
    finished_at timestamptz,
    UNIQUE(owner_id,source_digest)
);
CREATE TABLE memory_candidates (
    id uuid PRIMARY KEY,
    job_id uuid NOT NULL REFERENCES memory_jobs(id) ON DELETE CASCADE,
    owner_id uuid NOT NULL REFERENCES users(id),
    title text NOT NULL,
    content text NOT NULL,
    source_message_ids jsonb NOT NULL,
    status text NOT NULL DEFAULT 'PENDING' CHECK(status IN ('PENDING','ACCEPTED','REJECTED')),
    memory_id uuid REFERENCES private_memories(id) ON DELETE SET NULL,
    revision integer NOT NULL DEFAULT 1,
    created_at timestamptz NOT NULL DEFAULT now(),
    reviewed_at timestamptz
);
ALTER TABLE private_memories ADD COLUMN candidate_id uuid REFERENCES memory_candidates(id) ON DELETE SET NULL;
CREATE INDEX memory_jobs_pending ON memory_jobs(created_at) WHERE status='PENDING';
CREATE INDEX memory_candidates_owner ON memory_candidates(owner_id,created_at DESC);
CREATE INDEX memory_scope ON private_memories(owner_id,scope_kind,scope_id);
