ALTER TABLE work_queue_runs ADD COLUMN agent_snapshot jsonb NOT NULL DEFAULT '{}';
