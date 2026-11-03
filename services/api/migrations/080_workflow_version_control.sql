ALTER TABLE workflow_versions ADD COLUMN layout jsonb NOT NULL DEFAULT '{}';
ALTER TABLE workflows ADD COLUMN current_published_version integer;
ALTER TABLE workflows ADD COLUMN release_revision integer NOT NULL DEFAULT 1 CHECK(release_revision>=1);
ALTER TABLE workflows ADD CONSTRAINT workflows_current_version_fk FOREIGN KEY(id,current_published_version)
 REFERENCES workflow_versions(workflow_id,version);
