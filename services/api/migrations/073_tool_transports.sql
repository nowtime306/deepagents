ALTER TABLE mcp_servers ADD COLUMN transport text NOT NULL DEFAULT 'streamable_http'
 CHECK(transport IN ('streamable_http','sse','stdio','acp'));
ALTER TABLE mcp_servers ADD COLUMN oauth_required boolean NOT NULL DEFAULT false;
ALTER TABLE mcp_servers ADD COLUMN command_json jsonb NOT NULL DEFAULT '[]';
CREATE TABLE mcp_oauth_clients (
 server_id uuid PRIMARY KEY REFERENCES mcp_servers(id) ON DELETE CASCADE,
 client_id text NOT NULL, secret_ciphertext text NOT NULL DEFAULT '', scopes text NOT NULL DEFAULT '',
 revision integer NOT NULL DEFAULT 1
);
CREATE TABLE mcp_oauth_flows (
 state_hash text PRIMARY KEY, owner_id uuid NOT NULL REFERENCES users(id) ON DELETE CASCADE,
 server_id uuid NOT NULL REFERENCES mcp_servers(id) ON DELETE CASCADE,
 config_pin text NOT NULL, client_revision integer NOT NULL,
 verifier_ciphertext text NOT NULL, metadata jsonb NOT NULL, expires_at timestamptz NOT NULL,
 consumed_at timestamptz
);
CREATE TABLE mcp_oauth_grants (
 owner_id uuid NOT NULL REFERENCES users(id) ON DELETE CASCADE,
 server_id uuid NOT NULL REFERENCES mcp_servers(id) ON DELETE CASCADE,
 config_pin text NOT NULL, client_revision integer NOT NULL, issuer text NOT NULL,
 token_endpoint text NOT NULL, revoke_endpoint text,
 access_ciphertext text NOT NULL, refresh_ciphertext text NOT NULL DEFAULT '',
 scopes text NOT NULL, expires_at timestamptz NOT NULL,
 revision integer NOT NULL DEFAULT 1, PRIMARY KEY(owner_id,server_id)
);
