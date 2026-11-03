ALTER TABLE api_credentials DROP CONSTRAINT api_credentials_version_id_fkey;
ALTER TABLE api_credentials ALTER COLUMN version_id DROP NOT NULL;
ALTER TABLE api_credentials ADD CONSTRAINT api_credentials_version_id_fkey FOREIGN KEY(version_id) REFERENCES workflow_versions(id) ON DELETE SET NULL;
