CREATE TABLE work_sandbox_namespace (
    singleton boolean PRIMARY KEY DEFAULT true CHECK(singleton),
    id uuid NOT NULL DEFAULT gen_random_uuid()
);
INSERT INTO work_sandbox_namespace(singleton) VALUES(true);
CREATE TABLE work_sandbox_sessions (
    id uuid PRIMARY KEY,
    run_id uuid NOT NULL REFERENCES work_queue_runs(id) ON DELETE RESTRICT,
    owner_id uuid NOT NULL REFERENCES users(id),
    kind text NOT NULL CHECK(kind IN ('coding','browser')),
    sandbox_id text UNIQUE,
    state text NOT NULL CHECK(state IN ('CREATING','READY','UNKNOWN','CLOSING','RELEASED')),
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now()
);
CREATE UNIQUE INDEX work_sandbox_capacity ON work_sandbox_sessions(kind) WHERE state<>'RELEASED';
CREATE INDEX work_sandbox_run ON work_sandbox_sessions(run_id);
