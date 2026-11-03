ALTER TABLE model_usage ADD COLUMN cache_read_tokens bigint CHECK(cache_read_tokens>=0);
ALTER TABLE model_usage ADD COLUMN cache_creation_tokens bigint CHECK(cache_creation_tokens>=0);
ALTER TABLE model_usage ADD COLUMN reasoning_tokens bigint CHECK(reasoning_tokens>=0);
ALTER TABLE model_usage ADD COLUMN first_token_at timestamptz;
ALTER TABLE model_usage ADD COLUMN failure_code text;
ALTER TABLE task_runs ADD COLUMN harness_policy jsonb;
