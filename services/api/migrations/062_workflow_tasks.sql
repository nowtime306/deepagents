ALTER TABLE work_queue_runs DROP CONSTRAINT work_queue_runs_step_limit_check;
ALTER TABLE work_queue_runs ADD CONSTRAINT work_queue_runs_step_limit_check CHECK(step_limit BETWEEN 1 AND 100);
CREATE TABLE work_run_workflows (
    run_id uuid PRIMARY KEY REFERENCES work_queue_runs(id) ON DELETE CASCADE,
    workflow_run_id uuid NOT NULL UNIQUE REFERENCES workflow_runs(id) ON DELETE CASCADE,
    version_id uuid
);
