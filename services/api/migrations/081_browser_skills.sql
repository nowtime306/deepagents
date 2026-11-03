ALTER TABLE work_queue_runs ADD COLUMN allow_local_browser boolean NOT NULL DEFAULT false;
ALTER TABLE work_queue_runs ADD CONSTRAINT work_local_browser_environment
 CHECK(NOT allow_local_browser OR environment='browser');
CREATE TABLE browser_skill_recordings (
 id uuid PRIMARY KEY, owner_id uuid NOT NULL REFERENCES users(id),
 connection_id text NOT NULL, title text NOT NULL, description text NOT NULL DEFAULT '',
 status text NOT NULL CHECK(status IN ('starting','recording','stopping','review','failed','cancelled')),
 revision integer NOT NULL DEFAULT 1, draft jsonb NOT NULL DEFAULT '{"steps":[],"parameters":[]}',
 error_code text, skill_id uuid REFERENCES skills(id) ON DELETE SET NULL, skill_version integer,
 created_at timestamptz NOT NULL DEFAULT now(), updated_at timestamptz NOT NULL DEFAULT now()
);
CREATE UNIQUE INDEX browser_skill_connection_busy ON browser_skill_recordings(connection_id)
 WHERE status IN ('starting','recording','stopping');
CREATE INDEX browser_skill_recordings_owner ON browser_skill_recordings(owner_id,created_at DESC);
CREATE TABLE browser_skill_versions (
 skill_id uuid NOT NULL REFERENCES skills(id) ON DELETE CASCADE, version integer NOT NULL,
 owner_id uuid NOT NULL REFERENCES users(id), recording_id uuid REFERENCES browser_skill_recordings(id) ON DELETE SET NULL,
 trajectory jsonb NOT NULL, PRIMARY KEY(skill_id,version)
);
