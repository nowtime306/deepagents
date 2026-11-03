CREATE TABLE identity_mfa (
 owner_id uuid PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
 secret_ciphertext text NOT NULL, enabled boolean NOT NULL DEFAULT false,
 last_step bigint NOT NULL DEFAULT -1, recovery_hashes jsonb NOT NULL DEFAULT '[]',
 created_at timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE identity_challenges (
 token_hash text PRIMARY KEY, owner_id uuid NOT NULL REFERENCES users(id) ON DELETE CASCADE,
 expires_at timestamptz NOT NULL, created_at timestamptz NOT NULL DEFAULT now(), password_version text NOT NULL
);
CREATE TABLE identity_providers (
 id uuid PRIMARY KEY, name text NOT NULL, issuer text NOT NULL, client_id text NOT NULL,
 secret_ciphertext text NOT NULL DEFAULT '', enabled boolean NOT NULL DEFAULT false,
 allow_registration boolean NOT NULL DEFAULT false, allowed_domains text[] NOT NULL DEFAULT '{}',
 revision integer NOT NULL DEFAULT 1
);
CREATE TABLE identity_oidc_states (
 token_hash text PRIMARY KEY, provider_id uuid NOT NULL REFERENCES identity_providers(id) ON DELETE CASCADE,
 provider_revision integer NOT NULL, nonce text NOT NULL, verifier_ciphertext text NOT NULL,
 owner_id uuid REFERENCES users(id) ON DELETE CASCADE, browser_hash text NOT NULL,
 expires_at timestamptz NOT NULL,
 binding_session_hash text, binding_password_version text,
 CHECK(owner_id IS NULL OR (binding_session_hash IS NOT NULL AND binding_password_version IS NOT NULL))
);
CREATE TABLE identity_bindings (
 provider_id uuid NOT NULL REFERENCES identity_providers(id) ON DELETE CASCADE,
 subject text NOT NULL, owner_id uuid NOT NULL REFERENCES users(id) ON DELETE CASCADE,
 PRIMARY KEY(provider_id,subject), UNIQUE(provider_id,owner_id)
);
ALTER TABLE identity_challenges ADD COLUMN provider_id uuid REFERENCES identity_providers(id) ON DELETE CASCADE;
ALTER TABLE identity_challenges ADD COLUMN provider_revision integer;
CREATE TABLE identity_provider_sessions (
 token_hash text PRIMARY KEY REFERENCES auth_sessions(token_hash) ON DELETE CASCADE,
 provider_id uuid NOT NULL REFERENCES identity_providers(id) ON DELETE CASCADE
);
CREATE TABLE identity_channels (
 kind text PRIMARY KEY CHECK(kind IN ('email','sms')), name text NOT NULL,
 config_ciphertext text NOT NULL, enabled boolean NOT NULL DEFAULT false
);
CREATE TABLE identity_contacts (
 owner_id uuid NOT NULL REFERENCES users(id) ON DELETE CASCADE,
 kind text NOT NULL CHECK(kind IN ('email','sms')), address_ciphertext text NOT NULL,
 address_hash text NOT NULL, verified boolean NOT NULL DEFAULT false,
 PRIMARY KEY(owner_id,kind), UNIQUE(kind,address_hash)
);
CREATE TABLE identity_recovery_tokens (
 token_hash text PRIMARY KEY, owner_id uuid NOT NULL REFERENCES users(id) ON DELETE CASCADE,
 kind text NOT NULL, purpose text NOT NULL CHECK(purpose IN ('verify','reset')),
 expires_at timestamptz NOT NULL, address_hash text
);
CREATE TABLE identity_pending_contacts (
 owner_id uuid NOT NULL REFERENCES users(id) ON DELETE CASCADE,
 kind text NOT NULL CHECK(kind IN ('email','sms')), address_ciphertext text NOT NULL, address_hash text NOT NULL,
 PRIMARY KEY(owner_id,kind)
);
CREATE TABLE identity_deliveries (
 id uuid PRIMARY KEY, token_hash text NOT NULL REFERENCES identity_recovery_tokens(token_hash) ON DELETE CASCADE,
 payload_ciphertext text NOT NULL, attempts integer NOT NULL DEFAULT 0,
 status text NOT NULL DEFAULT 'pending' CHECK(status IN ('pending','sending','sent','failed','unknown')),
 error_code text,
 available_at timestamptz NOT NULL DEFAULT now(), created_at timestamptz NOT NULL DEFAULT now()
);
