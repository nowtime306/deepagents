CREATE TABLE agents (
    id uuid PRIMARY KEY,
    owner_id uuid NOT NULL REFERENCES users(id),
    name text NOT NULL,
    instructions text NOT NULL DEFAULT '',
    model_id text NOT NULL,
    enabled boolean NOT NULL DEFAULT true,
    revision integer NOT NULL DEFAULT 1,
    created_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE(owner_id, name)
);
ALTER TABLE conversations ADD COLUMN agent_id uuid REFERENCES agents(id);
ALTER TABLE task_runs ADD COLUMN agent_snapshot jsonb;
