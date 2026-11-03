CREATE TABLE platform_roles (
    id text PRIMARY KEY,
    name text NOT NULL,
    permissions jsonb NOT NULL CHECK (jsonb_typeof(permissions)='array'),
    built_in boolean NOT NULL DEFAULT false,
    enabled boolean NOT NULL DEFAULT true,
    created_at timestamptz NOT NULL DEFAULT now()
);
INSERT INTO platform_roles(id,name,permissions,built_in) VALUES
('admin','平台管理员','["*"]',true),
('member','成员','["chat.read","chat.manage","chat.run","work.read","work.manage","work.execute","agents.read","agents.manage","knowledge.read","knowledge.manage","knowledge.search","skills.read","skills.manage","tools.read","tools.use","workflows.read","workflows.manage","workflows.execute","memory.read","memory.manage","media.read","media.use","organizations.read","organizations.manage","operations.read"]',true),
('viewer','只读成员','["chat.read","work.read","agents.read","knowledge.read","skills.read","tools.read","workflows.read","memory.read","media.read","organizations.read","operations.read"]',true);
ALTER TABLE users ADD COLUMN role_id text NOT NULL DEFAULT 'member' REFERENCES platform_roles(id);
ALTER TABLE users ADD COLUMN enabled boolean NOT NULL DEFAULT true;
ALTER TABLE users ADD COLUMN approval_status text NOT NULL DEFAULT 'approved'
    CHECK (approval_status IN ('pending','approved','rejected'));
ALTER TABLE users ADD COLUMN must_change_password boolean NOT NULL DEFAULT false;
UPDATE users SET role_id='admin' WHERE is_admin;
CREATE UNIQUE INDEX users_username_folded ON users(lower(username));
ALTER TABLE auth_sessions ADD COLUMN id uuid NOT NULL DEFAULT gen_random_uuid();
CREATE UNIQUE INDEX auth_sessions_id ON auth_sessions(id);
ALTER TABLE auth_sessions ADD COLUMN created_at timestamptz;
ALTER TABLE auth_sessions ALTER COLUMN created_at SET DEFAULT now();
CREATE TABLE account_settings (
    id integer PRIMARY KEY CHECK (id=1),
    registration_mode text NOT NULL CHECK (registration_mode IN ('open','approval','closed'))
);
INSERT INTO account_settings VALUES(1,'approval');
CREATE TABLE account_invitations (
    id uuid PRIMARY KEY,
    name text NOT NULL,
    token_hash text NOT NULL UNIQUE,
    token_hint text NOT NULL,
    role_id text NOT NULL REFERENCES platform_roles(id),
    created_by uuid NOT NULL REFERENCES users(id),
    expires_at timestamptz NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    used_by uuid REFERENCES users(id),
    used_at timestamptz,
    revoked_at timestamptz
);
CREATE TABLE auth_rate_windows (
    key text PRIMARY KEY,
    hits integer NOT NULL,
    expires_at timestamptz NOT NULL
);
CREATE INDEX auth_rate_expiry ON auth_rate_windows(expires_at);
