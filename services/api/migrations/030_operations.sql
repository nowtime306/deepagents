CREATE TABLE audit_events (
 id bigserial PRIMARY KEY, actor_id uuid NOT NULL REFERENCES users(id),
 method text NOT NULL, resource_path text NOT NULL, status_code integer NOT NULL,
 created_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX audit_actor ON audit_events(actor_id,id DESC);
CREATE TABLE model_cost_rates (
 model_id text PRIMARY KEY REFERENCES model_configs(id) ON DELETE CASCADE,
 input_per_million numeric(18,6) NOT NULL CHECK(input_per_million>=0),
 output_per_million numeric(18,6) NOT NULL CHECK(output_per_million>=0),
 currency text NOT NULL CHECK(currency IN ('CNY','USD')), updated_at timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE model_usage (
 call_id uuid PRIMARY KEY, run_id uuid NOT NULL, owner_id uuid NOT NULL REFERENCES users(id),
 mode text NOT NULL CHECK(mode IN ('ask','work','workflow')), model_id text NOT NULL,
 status text NOT NULL DEFAULT 'PENDING', input_tokens bigint, output_tokens bigint,
 input_rate numeric(18,6), output_rate numeric(18,6), currency text, estimated_cost numeric(24,9),
 created_at timestamptz NOT NULL DEFAULT now(), finished_at timestamptz
);
CREATE INDEX model_usage_owner ON model_usage(owner_id,created_at DESC);
