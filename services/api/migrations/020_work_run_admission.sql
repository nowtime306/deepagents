CREATE UNIQUE INDEX work_task_one_live_run ON work_queue_runs(task_id)
WHERE status IN ('PENDING','RUNNING','WAITING_APPROVAL');
