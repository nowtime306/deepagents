CREATE TABLE repositories (
    id uuid PRIMARY KEY,
    owner_id uuid NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    name text NOT NULL,
    slug text NOT NULL,
    ref text NOT NULL,
    subdirectory text NOT NULL DEFAULT '',
    token_ciphertext text NOT NULL DEFAULT '',
    enabled boolean NOT NULL DEFAULT true,
    revision integer NOT NULL DEFAULT 1,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX repositories_owner ON repositories(owner_id,updated_at DESC);
CREATE TABLE work_run_repositories (
    run_id uuid PRIMARY KEY REFERENCES work_queue_runs(id) ON DELETE CASCADE,
    repository_id uuid NOT NULL REFERENCES repositories(id),
    revision integer NOT NULL,
    source jsonb NOT NULL,
    branch text NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE work_git_snapshots (
    snapshot_id uuid PRIMARY KEY REFERENCES work_snapshots(id) ON DELETE CASCADE,
    id uuid UNIQUE NOT NULL,
    digest text NOT NULL,
    size bigint NOT NULL CHECK(size BETWEEN 1 AND 20971520),
    metadata jsonb NOT NULL
);
CREATE TABLE repository_actions (
    id uuid PRIMARY KEY,
    run_id uuid NOT NULL REFERENCES work_queue_runs(id) ON DELETE CASCADE,
    owner_id uuid NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    kind text NOT NULL CHECK(kind IN ('push','pr')),
    status text NOT NULL DEFAULT 'PREPARED' CHECK(status IN ('PREPARED','STARTED','SUCCEEDED','REJECTED','UNKNOWN','FAILED')),
    payload jsonb NOT NULL,
    digest text NOT NULL,
    result jsonb NOT NULL DEFAULT '{}',
    error_code text,
    expires_at timestamptz NOT NULL DEFAULT now()+interval '15 minutes',
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now()
);
CREATE UNIQUE INDEX repository_action_active ON repository_actions(run_id,kind)
    WHERE status IN ('PREPARED','STARTED','UNKNOWN');
