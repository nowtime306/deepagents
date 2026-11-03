ALTER TABLE work_queue_runs ADD COLUMN network_domains jsonb NOT NULL DEFAULT '[]';
