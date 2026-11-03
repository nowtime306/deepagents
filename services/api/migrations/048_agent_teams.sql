ALTER TABLE agents ADD COLUMN team jsonb NOT NULL DEFAULT '[]' CHECK (jsonb_typeof(team)='array');
CREATE TABLE team_calls (
    id uuid PRIMARY KEY,
    owner_id uuid NOT NULL REFERENCES users(id),
    ask_run_id uuid REFERENCES task_runs(id) ON DELETE CASCADE,
    work_run_id uuid REFERENCES work_queue_runs(id) ON DELETE CASCADE,
    lease_token uuid,
    tool_call_id text NOT NULL,
    input_digest text NOT NULL,
    role_name text NOT NULL,
    version_id uuid NOT NULL,
    model_id text NOT NULL,
    status text NOT NULL DEFAULT 'RUNNING' CHECK(status IN ('RUNNING','SUCCEEDED','FAILED','CANCELLED','UNKNOWN')),
    cancel_requested boolean NOT NULL DEFAULT false,
    result text,
    error_code text,
    created_at timestamptz NOT NULL DEFAULT now(),
    finished_at timestamptz,
    CHECK ((ask_run_id IS NULL) <> (work_run_id IS NULL)),
    UNIQUE(ask_run_id,tool_call_id),
    UNIQUE(work_run_id,tool_call_id)
);
ALTER TABLE model_usage ADD COLUMN role_name text NOT NULL DEFAULT 'lead';
ALTER TABLE model_usage ADD COLUMN team_call_id uuid REFERENCES team_calls(id) ON DELETE SET NULL;
CREATE INDEX team_calls_owner_run ON team_calls(owner_id,ask_run_id,work_run_id);
