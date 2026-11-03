ALTER TABLE agents ADD COLUMN workflow_version_ids jsonb NOT NULL DEFAULT '[]';
CREATE UNIQUE INDEX work_workflow_credentials ON api_credentials(parent_work_run,parent_lease_token,version_id) WHERE internal_only AND parent_work_run IS NOT NULL;
