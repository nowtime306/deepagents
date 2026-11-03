CREATE TABLE agent_versions (
 id uuid PRIMARY KEY,
 agent_id uuid NOT NULL REFERENCES agents(id) ON DELETE CASCADE,
 owner_id uuid NOT NULL REFERENCES users(id),
 version integer NOT NULL CHECK(version>0),
 revision integer NOT NULL,
 snapshot jsonb NOT NULL,
 active boolean NOT NULL DEFAULT true,
 created_at timestamptz NOT NULL DEFAULT now(),
 UNIQUE(agent_id,version)
);
ALTER TABLE conversations ADD COLUMN agent_version_id uuid REFERENCES agent_versions(id);
