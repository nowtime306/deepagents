ALTER TABLE model_usage DROP CONSTRAINT model_usage_mode_check;
ALTER TABLE model_usage ADD CONSTRAINT model_usage_mode_check CHECK(mode IN ('ask','work','workflow','resource'));
ALTER TABLE model_usage ADD COLUMN call_kind text NOT NULL DEFAULT 'chat' CHECK(call_kind IN ('chat','embedding'));
ALTER TABLE work_queue_runs ADD COLUMN resource_budgeted boolean NOT NULL DEFAULT false;
CREATE TABLE resource_operations (
    id uuid PRIMARY KEY,
    owner_id uuid NOT NULL REFERENCES users(id),
    resource_kind text NOT NULL CHECK(resource_kind IN ('memory','knowledge')),
    action text NOT NULL CHECK(action IN ('index','query')),
    model_id text NOT NULL,
    model_revision integer NOT NULL,
    status text NOT NULL DEFAULT 'RUNNING' CHECK(status IN ('RUNNING','SUCCEEDED','FAILED','UNKNOWN')),
    lease_token uuid NOT NULL,
    lease_until timestamptz NOT NULL,
    cancel_requested boolean NOT NULL DEFAULT false,
    model_call_limit integer NOT NULL DEFAULT 2,
    token_limit integer NOT NULL DEFAULT 64000,
    error_code text,
    created_at timestamptz NOT NULL DEFAULT now(),
    finished_at timestamptz
);
CREATE INDEX resource_operations_owner ON resource_operations(owner_id,created_at DESC);
CREATE TABLE memory_settings (
    owner_id uuid PRIMARY KEY REFERENCES users(id),
    embedding_model_id text,
    model_revision integer,
    enabled boolean NOT NULL DEFAULT false,
    revision integer NOT NULL DEFAULT 1
);
ALTER TABLE private_memories ADD COLUMN vector jsonb;
ALTER TABLE private_memories ADD COLUMN vector_model_id text;
ALTER TABLE private_memories ADD COLUMN vector_model_revision integer;
ALTER TABLE private_memories ADD COLUMN vector_digest text;
ALTER TABLE private_memories ADD COLUMN vector_status text NOT NULL DEFAULT 'NONE' CHECK(vector_status IN ('NONE','READY','FAILED'));
