ALTER TABLE model_usage DROP CONSTRAINT model_usage_call_kind_check;
ALTER TABLE model_usage ADD CONSTRAINT model_usage_call_kind_check CHECK(call_kind IN ('chat','embedding','rerank'));
ALTER TABLE resource_operations ADD COLUMN resource_id uuid;
ALTER TABLE resource_operations ADD COLUMN source_id uuid;
ALTER TABLE resource_operations ADD COLUMN source_revision integer;
CREATE INDEX resource_operation_source ON resource_operations(owner_id,source_id,created_at DESC);
