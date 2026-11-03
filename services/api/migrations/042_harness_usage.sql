-- Estimates are separate from provider-reported statistics; existing rows stay NULL.
ALTER TABLE model_usage ADD COLUMN estimated_input_tokens bigint;
ALTER TABLE model_usage ADD COLUMN output_token_reserve bigint;
CREATE INDEX model_usage_run ON model_usage(owner_id,run_id);
