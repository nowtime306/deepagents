CREATE TABLE workflow_versions (
    id uuid PRIMARY KEY, workflow_id uuid NOT NULL REFERENCES workflows(id) ON DELETE CASCADE,
    version integer NOT NULL, draft_revision integer NOT NULL, model_id text NOT NULL,
    model_revision integer NOT NULL, name text NOT NULL, instructions text NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(), UNIQUE(workflow_id,version), UNIQUE(workflow_id,draft_revision,model_revision)
);

