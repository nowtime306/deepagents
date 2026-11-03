CREATE TABLE model_capability_declarations (
 model_id text PRIMARY KEY REFERENCES model_configs(id) ON DELETE CASCADE,
 supports_tools boolean
);
CREATE TABLE model_routes (
 id uuid PRIMARY KEY, name text NOT NULL, primary_model text UNIQUE NOT NULL REFERENCES model_configs(id) ON DELETE CASCADE,
 candidates jsonb NOT NULL, revision integer NOT NULL DEFAULT 1,
 failures integer NOT NULL CHECK(failures BETWEEN 1 AND 20), cooldown integer NOT NULL CHECK(cooldown BETWEEN 10 AND 3600),
 require_images boolean NOT NULL DEFAULT false, require_tools boolean NOT NULL DEFAULT false,
 require_native boolean NOT NULL DEFAULT false, enabled boolean NOT NULL DEFAULT true,
 updated_at timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE model_route_consents (
 owner_id uuid NOT NULL REFERENCES users(id) ON DELETE CASCADE,
 route_id uuid NOT NULL REFERENCES model_routes(id) ON DELETE CASCADE,
 digest text NOT NULL, accepted_at timestamptz NOT NULL DEFAULT now(), PRIMARY KEY(owner_id,route_id)
);
CREATE TABLE model_route_selections (
 id uuid PRIMARY KEY, owner_id uuid NOT NULL REFERENCES users(id) ON DELETE CASCADE, run_id uuid NOT NULL,
 scope text NOT NULL, route_id uuid NOT NULL REFERENCES model_routes(id) ON DELETE CASCADE,
 digest text NOT NULL, model_id text NOT NULL REFERENCES model_configs(id) ON DELETE CASCADE,
 model_revision integer NOT NULL, reason jsonb NOT NULL, created_at timestamptz NOT NULL DEFAULT now(),
 UNIQUE(owner_id,run_id,scope)
);
CREATE TABLE model_route_probes (
 model_id text NOT NULL REFERENCES model_configs(id) ON DELETE CASCADE, model_revision integer NOT NULL,
 selection_id uuid NOT NULL, until_at timestamptz NOT NULL, PRIMARY KEY(model_id,model_revision)
);
ALTER TABLE agents ADD COLUMN model_routing boolean NOT NULL DEFAULT false;
ALTER TABLE model_usage ADD COLUMN route_selection_id uuid REFERENCES model_route_selections(id) ON DELETE SET NULL;
CREATE INDEX model_routing_health ON model_usage(model_id,model_revision,created_at DESC);
