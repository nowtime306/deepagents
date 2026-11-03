ALTER TABLE workflows ADD COLUMN definition jsonb NOT NULL DEFAULT '{"nodes":[{"id":"start","kind":"start"},{"id":"llm","kind":"llm"},{"id":"end","kind":"end","input":"llm"}],"edges":[{"source":"start","target":"llm"},{"source":"llm","target":"end"}]}';
ALTER TABLE workflows ADD COLUMN layout jsonb NOT NULL DEFAULT '{}';
ALTER TABLE workflows ADD COLUMN tested_revision integer;
ALTER TABLE workflows ADD COLUMN tested_pins jsonb;
ALTER TABLE workflow_versions ADD COLUMN definition jsonb NOT NULL DEFAULT '{"nodes":[{"id":"start","kind":"start"},{"id":"llm","kind":"llm"},{"id":"end","kind":"end","input":"llm"}],"edges":[{"source":"start","target":"llm"},{"source":"llm","target":"end"}]}';
ALTER TABLE workflow_versions ADD COLUMN resource_pins jsonb NOT NULL DEFAULT '{}';
ALTER TABLE workflow_versions ADD COLUMN digest text NOT NULL DEFAULT '';
ALTER TABLE workflow_versions ADD COLUMN active boolean NOT NULL DEFAULT true;
ALTER TABLE workflow_runs ADD COLUMN error_node text;
CREATE TABLE workflow_node_events (
    event_id bigserial PRIMARY KEY,run_id uuid NOT NULL REFERENCES workflow_runs(id) ON DELETE CASCADE,
    node_id text NOT NULL,event text NOT NULL,data jsonb NOT NULL,created_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX workflow_node_events_run ON workflow_node_events(run_id,event_id);
