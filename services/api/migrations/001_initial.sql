CREATE TABLE users (
    id uuid PRIMARY KEY,
    username text NOT NULL UNIQUE,
    password_hash text NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE auth_sessions (
    token_hash text PRIMARY KEY,
    owner_id uuid NOT NULL REFERENCES users(id),
    expires_at timestamptz NOT NULL
);
CREATE TABLE conversations (
    id uuid PRIMARY KEY,
    owner_id uuid NOT NULL REFERENCES users(id),
    title text NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX conversations_owner ON conversations(owner_id, created_at DESC);
CREATE TABLE task_runs (
    id uuid PRIMARY KEY,
    conversation_id uuid NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
    model_id text NOT NULL,
    status text NOT NULL CHECK(status IN ('PENDING','RUNNING','SUCCEEDED','FAILED','CANCELLED')),
    cancel_requested boolean NOT NULL DEFAULT false,
    next_event_id bigint NOT NULL DEFAULT 0,
    error_code text,
    created_at timestamptz NOT NULL DEFAULT now(),
    finished_at timestamptz
);
CREATE UNIQUE INDEX one_active_run ON task_runs(conversation_id)
    WHERE status IN ('PENDING','RUNNING');
CREATE TABLE conversation_messages (
    id uuid PRIMARY KEY,
    conversation_id uuid NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
    run_id uuid NOT NULL REFERENCES task_runs(id) ON DELETE CASCADE,
    role text NOT NULL,
    content jsonb NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE run_events (
    run_id uuid NOT NULL REFERENCES task_runs(id) ON DELETE CASCADE,
    event_id bigint NOT NULL,
    event text NOT NULL,
    data jsonb NOT NULL,
    timestamp timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY(run_id, event_id)
);
