ALTER TABLE work_queue_runs DROP CONSTRAINT work_queue_runs_status_check;
ALTER TABLE work_queue_runs ADD CONSTRAINT work_queue_runs_status_check CHECK (
    status IN ('PENDING','RUNNING','NEEDS_REVIEW','WAITING_APPROVAL','PAUSED','COMPLETED','FAILED','CANCELLED')
);
ALTER TABLE work_queue_runs ADD COLUMN model_id text;
ALTER TABLE work_queue_runs ADD COLUMN model_revision integer;
ALTER TABLE work_queue_runs ADD COLUMN environment text NOT NULL DEFAULT 'coding' CHECK(environment IN ('coding','browser'));
ALTER TABLE work_queue_runs ADD COLUMN instructions text NOT NULL DEFAULT '';
ALTER TABLE work_queue_runs ADD COLUMN resume_decision jsonb;
ALTER TABLE work_queue_runs ADD COLUMN cancel_requested boolean NOT NULL DEFAULT false;
ALTER TABLE work_queue_runs ADD COLUMN step_limit integer NOT NULL DEFAULT 40 CHECK(step_limit BETWEEN 5 AND 100);
ALTER TABLE work_sandbox_sessions ADD COLUMN retention_required boolean NOT NULL DEFAULT false;
CREATE TABLE work_approvals (
    id uuid PRIMARY KEY, run_id uuid NOT NULL REFERENCES work_queue_runs(id) ON DELETE CASCADE,
    owner_id uuid NOT NULL REFERENCES users(id), interrupt_id text NOT NULL,
    payload jsonb NOT NULL, digest text NOT NULL, expires_at timestamptz NOT NULL,
    decision text CHECK(decision IN ('approve','reject')), decided_at timestamptz, consumed_at timestamptz,
    created_at timestamptz NOT NULL DEFAULT now(), UNIQUE(run_id,interrupt_id)
);
CREATE TABLE work_snapshots (
    id uuid PRIMARY KEY, run_id uuid NOT NULL REFERENCES work_queue_runs(id) ON DELETE CASCADE,
    created_at timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE work_artifacts (
    id uuid PRIMARY KEY, snapshot_id uuid NOT NULL REFERENCES work_snapshots(id) ON DELETE CASCADE,
    run_id uuid NOT NULL REFERENCES work_queue_runs(id) ON DELETE CASCADE,
    owner_id uuid NOT NULL REFERENCES users(id), path text NOT NULL, digest text NOT NULL,
    size integer NOT NULL CHECK(size>=0 AND size<=1048576), created_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE(snapshot_id,path)
);
