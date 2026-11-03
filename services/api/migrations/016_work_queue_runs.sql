CREATE TABLE work_queue_runs (
    id uuid PRIMARY KEY,
    task_id uuid NOT NULL REFERENCES work_tasks(id) ON DELETE RESTRICT,
    owner_id uuid NOT NULL REFERENCES users(id),
    title text NOT NULL,
    goal text NOT NULL,
    task_revision integer NOT NULL,
    status text NOT NULL DEFAULT 'PENDING' CHECK (status IN ('PENDING','RUNNING','NEEDS_REVIEW','COMPLETED','FAILED')),
    lease_token uuid,
    lease_until timestamptz,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    CHECK ((status = 'RUNNING') = (lease_token IS NOT NULL AND lease_until IS NOT NULL))
);
CREATE INDEX work_queue_pending ON work_queue_runs(created_at, id) WHERE status = 'PENDING';
CREATE INDEX work_queue_owner ON work_queue_runs(owner_id, created_at DESC);
