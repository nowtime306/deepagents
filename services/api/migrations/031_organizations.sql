CREATE TABLE organizations (id uuid PRIMARY KEY,name text NOT NULL,owner_id uuid NOT NULL REFERENCES users(id),created_at timestamptz NOT NULL DEFAULT now());
CREATE TABLE organization_members (
 organization_id uuid NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
 user_id uuid NOT NULL REFERENCES users(id),role text NOT NULL CHECK(role IN ('owner','admin','editor','viewer')),
 PRIMARY KEY(organization_id,user_id)
);
CREATE TABLE organization_workflows (
 organization_id uuid NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
 version_id uuid NOT NULL REFERENCES workflow_versions(id) ON DELETE CASCADE,
 shared_by uuid NOT NULL REFERENCES users(id),PRIMARY KEY(organization_id,version_id)
);
ALTER TABLE api_credentials ADD COLUMN internal_only boolean NOT NULL DEFAULT false;
ALTER TABLE api_credentials ADD COLUMN organization_id uuid REFERENCES organizations(id) ON DELETE CASCADE;
ALTER TABLE api_credentials ADD COLUMN caller_id uuid REFERENCES users(id);
CREATE UNIQUE INDEX organization_credentials ON api_credentials(organization_id,caller_id,version_id) WHERE internal_only;
ALTER TABLE api_credentials ADD COLUMN parent_work_run uuid REFERENCES work_queue_runs(id) ON DELETE SET NULL;
ALTER TABLE api_credentials ADD COLUMN parent_lease_token uuid;
