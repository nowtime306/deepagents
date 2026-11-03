CREATE TABLE habit_settings (
 owner_id uuid PRIMARY KEY REFERENCES users(id), enabled boolean NOT NULL DEFAULT false,
 meta_skill_id uuid REFERENCES skills(id) ON DELETE SET NULL,
 learning_after timestamptz NOT NULL DEFAULT now(), updated_at timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE habit_preferences (
 id uuid PRIMARY KEY, owner_id uuid NOT NULL REFERENCES users(id), kind text NOT NULL, value text NOT NULL,
 status text NOT NULL DEFAULT 'learning' CHECK(status IN ('learning','active','disabled','forgotten')),
 revision integer NOT NULL DEFAULT 1, manual_confirmed boolean NOT NULL DEFAULT false,
 skill_id uuid REFERENCES skills(id) ON DELETE SET NULL, skill_version integer,
 learning_after timestamptz NOT NULL DEFAULT '1970-01-01T00:00:00Z', updated_at timestamptz NOT NULL DEFAULT now(),
 UNIQUE(owner_id,kind)
);
ALTER TABLE skills ADD COLUMN habit_preference_id uuid REFERENCES habit_preferences(id);
CREATE TABLE habit_evidence (
 owner_id uuid NOT NULL REFERENCES users(id), preference_id uuid NOT NULL REFERENCES habit_preferences(id),
 message_id uuid NOT NULL REFERENCES conversation_messages(id) ON DELETE CASCADE,
 source_digest text NOT NULL, created_at timestamptz NOT NULL DEFAULT now(),
 PRIMARY KEY(preference_id,message_id)
);
CREATE INDEX habit_evidence_owner ON habit_evidence(owner_id);
