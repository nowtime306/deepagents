CREATE TABLE conversation_attachments (
    id uuid PRIMARY KEY, conversation_id uuid NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
    owner_id uuid NOT NULL REFERENCES users(id), name text NOT NULL, size integer NOT NULL CHECK(size BETWEEN 1 AND 4194304),
    digest text NOT NULL, extracted text NOT NULL, used_at timestamptz, created_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX attachment_conversation ON conversation_attachments(conversation_id,created_at);
ALTER TABLE task_runs ADD COLUMN attachment_snapshot jsonb NOT NULL DEFAULT '[]';
