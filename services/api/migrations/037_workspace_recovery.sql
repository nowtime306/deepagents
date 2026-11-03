ALTER TABLE work_sandbox_sessions ADD COLUMN data_lost boolean NOT NULL DEFAULT false;
CREATE TABLE work_recovery_jobs (
 run_id uuid PRIMARY KEY REFERENCES work_queue_runs(id) ON DELETE CASCADE,
 owner_id uuid NOT NULL REFERENCES users(id),status text NOT NULL DEFAULT 'PENDING',
 token uuid,lease_until timestamptz,not_before timestamptz NOT NULL DEFAULT now()+interval '90 seconds',
 snapshot_id uuid REFERENCES work_snapshots(id) ON DELETE SET NULL,error_code text,
 updated_at timestamptz NOT NULL DEFAULT now()
);
