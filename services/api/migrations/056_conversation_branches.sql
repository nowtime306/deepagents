ALTER TABLE task_runs ADD COLUMN context_import boolean NOT NULL DEFAULT false;

CREATE TABLE conversation_branches (
    conversation_id uuid PRIMARY KEY REFERENCES conversations(id) ON DELETE CASCADE,
    owner_id uuid NOT NULL REFERENCES users(id),
    source_id uuid REFERENCES conversations(id) ON DELETE SET NULL,
    source_message_id uuid REFERENCES conversation_messages(id) ON DELETE SET NULL,
    mode text NOT NULL CHECK(mode IN ('fork','edit')),
    request_id uuid NOT NULL,
    request_digest text NOT NULL,
    seed jsonb NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE(owner_id,request_id)
);
