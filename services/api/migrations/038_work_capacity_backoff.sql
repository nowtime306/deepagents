ALTER TABLE work_queue_runs ADD COLUMN available_after timestamptz NOT NULL DEFAULT now();
