ALTER TABLE users ADD COLUMN is_admin boolean NOT NULL DEFAULT false;
UPDATE users SET is_admin=true WHERE username='admin';

CREATE TABLE model_configs (
    id text PRIMARY KEY,
    name text NOT NULL UNIQUE,
    model text NOT NULL,
    base_url text NOT NULL,
    api_key_ciphertext text NOT NULL DEFAULT '',
    max_output_tokens integer CHECK(max_output_tokens>0),
    temperature double precision CHECK(temperature>=0 AND temperature<=2),
    enabled boolean NOT NULL DEFAULT true,
    is_default boolean NOT NULL DEFAULT false,
    connection_status text NOT NULL DEFAULT 'not_tested',
    checked_at timestamptz,
    revision integer NOT NULL DEFAULT 1,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    CHECK(NOT is_default OR enabled)
);
CREATE UNIQUE INDEX one_default_model ON model_configs(is_default) WHERE is_default;
