ALTER TABLE task_runs ADD COLUMN lease_token uuid;
ALTER TABLE task_runs ADD COLUMN lease_until timestamptz;
ALTER TABLE task_runs ADD COLUMN worker_id uuid;
ALTER TABLE task_runs ADD COLUMN claimed_at timestamptz;
ALTER TABLE task_runs ADD COLUMN recovery_of uuid REFERENCES task_runs(id) ON DELETE SET NULL;
CREATE INDEX ask_pending_queue ON task_runs(created_at,id) WHERE status='PENDING';
CREATE INDEX ask_expired_leases ON task_runs(lease_until) WHERE status='RUNNING' AND lease_token IS NOT NULL;
CREATE TABLE ask_worker_instances (
    id uuid PRIMARY KEY,
    heartbeat_at timestamptz NOT NULL DEFAULT now(),
    started_at timestamptz NOT NULL DEFAULT now()
);
