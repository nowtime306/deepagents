ALTER TABLE resource_operations ALTER COLUMN model_id DROP NOT NULL;
ALTER TABLE resource_operations ALTER COLUMN model_revision DROP NOT NULL;
CREATE TABLE knowledge_datasets (
    id uuid PRIMARY KEY,
    owner_id uuid NOT NULL REFERENCES users(id),
    knowledge_id uuid NOT NULL REFERENCES knowledge_bases(id) ON DELETE CASCADE,
    name text NOT NULL,
    dataset_type text NOT NULL CHECK(dataset_type IN ('user','synthetic')),
    questions jsonb NOT NULL,
    revision integer NOT NULL DEFAULT 1,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE knowledge_experiments (
    id uuid PRIMARY KEY,
    request_id uuid NOT NULL,
    request_digest text NOT NULL,
    owner_id uuid NOT NULL REFERENCES users(id),
    knowledge_id uuid NOT NULL REFERENCES knowledge_bases(id) ON DELETE CASCADE,
    dataset_id uuid REFERENCES knowledge_datasets(id) ON DELETE SET NULL,
    dataset_snapshot jsonb NOT NULL,
    resource_snapshot jsonb NOT NULL,
    operation_id uuid REFERENCES resource_operations(id) ON DELETE SET NULL,
    status text NOT NULL DEFAULT 'PENDING' CHECK(status IN ('PENDING','RUNNING','SUCCEEDED','FAILED','UNKNOWN','CANCELLED')),
    cancel_requested boolean NOT NULL DEFAULT false,
    lease_token uuid,
    lease_until timestamptz,
    results jsonb NOT NULL DEFAULT '[]',
    metrics jsonb,
    error_code text,
    created_at timestamptz NOT NULL DEFAULT now(),
    finished_at timestamptz,
    UNIQUE(owner_id,request_id)
);
CREATE INDEX knowledge_experiments_queue ON knowledge_experiments(status,created_at);
CREATE TABLE message_feedback (
    message_id uuid PRIMARY KEY REFERENCES conversation_messages(id) ON DELETE CASCADE,
    owner_id uuid NOT NULL REFERENCES users(id),
    rating text NOT NULL CHECK(rating IN ('helpful','unhelpful')),
    citations_correct boolean,
    refusal_correct boolean,
    comment text NOT NULL DEFAULT '',
    revision integer NOT NULL DEFAULT 1,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now()
);
