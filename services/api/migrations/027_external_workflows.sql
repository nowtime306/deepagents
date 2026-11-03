ALTER TABLE workflow_runs DROP CONSTRAINT workflow_runs_status_check;
ALTER TABLE workflow_runs ADD CONSTRAINT workflow_runs_status_check CHECK(status IN ('PENDING','RUNNING','SUCCEEDED','FAILED','NEEDS_REVIEW','CANCELLED'));
CREATE TABLE api_credentials (
 id uuid PRIMARY KEY, owner_id uuid NOT NULL REFERENCES users(id), name text NOT NULL,
 version_id uuid NOT NULL REFERENCES workflow_versions(id) ON DELETE CASCADE,
 token_hash text NOT NULL UNIQUE, token_hint text NOT NULL,
 expires_at timestamptz NOT NULL, revoked_at timestamptz,
 rpm integer NOT NULL CHECK(rpm BETWEEN 1 AND 120),
 concurrency integer NOT NULL CHECK(concurrency BETWEEN 1 AND 4),
 created_at timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE external_workflow_jobs (
 run_id uuid PRIMARY KEY REFERENCES workflow_runs(id) ON DELETE CASCADE,
 credential_id uuid NOT NULL REFERENCES api_credentials(id),
 idempotency_key text NOT NULL, request_hash text NOT NULL,
 lease_token uuid, lease_until timestamptz,
 UNIQUE(credential_id,idempotency_key)
);
CREATE TABLE api_call_logs (
 id bigserial PRIMARY KEY, credential_id uuid NOT NULL REFERENCES api_credentials(id),
 run_id uuid, outcome text NOT NULL, created_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX api_call_logs_limit ON api_call_logs(credential_id,created_at DESC);
