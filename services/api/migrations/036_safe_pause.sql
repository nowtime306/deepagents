ALTER TABLE work_queue_runs ADD COLUMN pause_requested boolean NOT NULL DEFAULT false;
DROP INDEX work_task_one_live_run;
CREATE UNIQUE INDEX work_task_one_live_run ON work_queue_runs(task_id) WHERE status IN ('PENDING','RUNNING','WAITING_APPROVAL','PAUSED');
