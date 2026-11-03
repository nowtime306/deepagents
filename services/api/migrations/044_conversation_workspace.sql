CREATE TABLE conversation_projects (
    id uuid PRIMARY KEY,
    owner_id uuid NOT NULL REFERENCES users(id),
    name text NOT NULL CHECK (length(btrim(name)) BETWEEN 1 AND 80),
    created_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (id, owner_id)
);
CREATE INDEX conversation_projects_owner ON conversation_projects(owner_id, created_at DESC, id DESC);
ALTER TABLE conversations ADD COLUMN project_id uuid;
ALTER TABLE conversations ADD COLUMN pinned boolean NOT NULL DEFAULT false;
ALTER TABLE conversations ADD CONSTRAINT conversations_project_owner
    FOREIGN KEY (project_id, owner_id) REFERENCES conversation_projects(id, owner_id)
    ON DELETE SET NULL (project_id);
CREATE INDEX conversations_workspace ON conversations(owner_id, archived, pinned DESC, created_at DESC, id DESC);
CREATE INDEX conversation_messages_search_owner ON conversation_messages(conversation_id);
