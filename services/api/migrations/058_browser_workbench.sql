CREATE TABLE work_browser_states (
    task_id uuid PRIMARY KEY REFERENCES work_tasks(id) ON DELETE CASCADE,
    owner_id uuid NOT NULL REFERENCES users(id),
    state_ciphertext text NOT NULL DEFAULT '',
    revision integer NOT NULL DEFAULT 0 CHECK(revision>=0),
    status text NOT NULL DEFAULT 'EMPTY' CHECK(status IN ('EMPTY','READY','REVIEW')),
    updated_at timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE work_browser_transports (
    session_id uuid PRIMARY KEY REFERENCES work_sandbox_sessions(id) ON DELETE CASCADE,
    task_id uuid NOT NULL REFERENCES work_tasks(id) ON DELETE CASCADE,
    owner_id uuid NOT NULL REFERENCES users(id),
    token_ciphertext text NOT NULL DEFAULT '',
    status text NOT NULL DEFAULT 'INITIALIZING' CHECK(status IN ('INITIALIZING','READY','UNKNOWN','CLOSED')),
    state_revision integer NOT NULL DEFAULT 0,
    updated_at timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE work_browser_controls (
    id uuid PRIMARY KEY,
    run_id uuid NOT NULL REFERENCES work_queue_runs(id) ON DELETE CASCADE,
    owner_id uuid NOT NULL REFERENCES users(id),
    request_id uuid NOT NULL,
    status text NOT NULL DEFAULT 'PENDING' CHECK(status IN ('PENDING','CREATING','READY','CLOSING','CLOSED','UNKNOWN')),
    lease_token uuid NOT NULL,
    lease_until timestamptz NOT NULL DEFAULT now()+interval '120 seconds',
    deadline timestamptz NOT NULL DEFAULT now()+interval '30 minutes',
    session_id uuid REFERENCES work_sandbox_sessions(id),
    error_code text,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE(owner_id,request_id)
);
CREATE UNIQUE INDEX browser_control_per_owner ON work_browser_controls(owner_id)
    WHERE status<>'CLOSED';
CREATE UNIQUE INDEX browser_control_per_run ON work_browser_controls(run_id)
    WHERE status<>'CLOSED';
CREATE TABLE work_browser_actions (
    control_id uuid NOT NULL REFERENCES work_browser_controls(id) ON DELETE CASCADE,
    request_id uuid NOT NULL,
    kind text NOT NULL,
    digest text NOT NULL,
    status text NOT NULL DEFAULT 'PENDING' CHECK(status IN ('PENDING','SUCCEEDED','UNKNOWN')),
    result jsonb,
    reviewed_at timestamptz,
    created_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY(control_id,request_id)
);
