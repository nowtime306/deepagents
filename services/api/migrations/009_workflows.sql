CREATE TABLE workflows (
    id uuid PRIMARY KEY, owner_id uuid NOT NULL REFERENCES users(id),
    name text NOT NULL, instructions text NOT NULL DEFAULT '', model_id text NOT NULL,
    revision integer NOT NULL DEFAULT 1, created_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE(owner_id,name)
);
CREATE TABLE workflow_runs (
    id uuid PRIMARY KEY, workflow_id uuid NOT NULL REFERENCES workflows(id) ON DELETE CASCADE,
    status text NOT NULL CHECK(status IN ('RUNNING','SUCCEEDED','FAILED')),
    input text NOT NULL, output text, snapshot jsonb NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(), finished_at timestamptz
);
