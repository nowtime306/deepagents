ALTER TABLE agents ADD COLUMN memory_enabled boolean NOT NULL DEFAULT false;
ALTER TABLE capability_bindings ADD COLUMN memory_enabled boolean NOT NULL DEFAULT false;
CREATE TABLE private_memories (
 id uuid PRIMARY KEY, owner_id uuid NOT NULL REFERENCES users(id) ON DELETE CASCADE,
 title text NOT NULL, content text NOT NULL, enabled boolean NOT NULL DEFAULT true,
 revision integer NOT NULL DEFAULT 1, created_at timestamptz NOT NULL DEFAULT now(),
 updated_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX private_memories_owner ON private_memories(owner_id,updated_at DESC,id);
