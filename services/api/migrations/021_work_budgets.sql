ALTER TABLE work_queue_runs ADD COLUMN token_limit integer NOT NULL DEFAULT 200000 CHECK(token_limit BETWEEN 1000 AND 1000000);
ALTER TABLE work_queue_runs ADD COLUMN used_tokens bigint NOT NULL DEFAULT 0;
ALTER TABLE work_queue_runs ADD COLUMN model_calls integer NOT NULL DEFAULT 0;
ALTER TABLE work_queue_runs ADD COLUMN usage_unknown boolean NOT NULL DEFAULT false;
CREATE TABLE work_worker_instances (id uuid PRIMARY KEY, heartbeat_at timestamptz NOT NULL DEFAULT now());
