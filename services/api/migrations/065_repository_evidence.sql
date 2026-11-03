CREATE TABLE repository_evidence (
    id uuid PRIMARY KEY,
    run_id uuid NOT NULL REFERENCES work_queue_runs(id) ON DELETE CASCADE,
    owner_id uuid NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    data jsonb NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX repository_evidence_run ON repository_evidence(run_id,created_at DESC);
