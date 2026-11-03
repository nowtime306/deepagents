ALTER TABLE service_instances DROP CONSTRAINT service_instances_kind_check;
ALTER TABLE service_instances ADD CHECK(kind IN ('api','ask','work','ops'));
ALTER TABLE service_events DROP CONSTRAINT service_events_kind_check;
ALTER TABLE service_events ADD CHECK(kind IN ('api','ask','work','ops'));
CREATE TABLE backup_settings (
 singleton boolean PRIMARY KEY DEFAULT true CHECK(singleton), enabled boolean NOT NULL DEFAULT false,
 hour integer NOT NULL DEFAULT 2 CHECK(hour BETWEEN 0 AND 23), minute integer NOT NULL DEFAULT 30 CHECK(minute BETWEEN 0 AND 59),
 retain integer NOT NULL DEFAULT 3 CHECK(retain BETWEEN 2 AND 30), revision integer NOT NULL DEFAULT 1,
 next_due_at timestamptz, updated_at timestamptz NOT NULL DEFAULT now()
);
INSERT INTO backup_settings(singleton) VALUES(true);
CREATE TABLE backup_jobs (
 id uuid PRIMARY KEY, owner_id uuid REFERENCES users(id) ON DELETE SET NULL, dedupe_key text UNIQUE,
 status text NOT NULL DEFAULT 'PENDING' CHECK(status IN ('PENDING','RUNNING','SUCCEEDED','FAILED','DELETED')),
 phase text NOT NULL DEFAULT 'QUEUED', reason text, lease_token uuid, attempt integer NOT NULL DEFAULT 0,
 available_at timestamptz NOT NULL DEFAULT now(), resume_services jsonb NOT NULL DEFAULT '[]',
 backup_name text, manifest_digest text, verification jsonb,
 created_at timestamptz NOT NULL DEFAULT now(), finished_at timestamptz, deleted_at timestamptz
);
CREATE UNIQUE INDEX one_active_backup ON backup_jobs((true)) WHERE status IN ('PENDING','RUNNING');
CREATE TABLE platform_maintenance (
 singleton boolean PRIMARY KEY DEFAULT true CHECK(singleton), job_id uuid REFERENCES backup_jobs(id),
 lease_token uuid, active boolean NOT NULL DEFAULT false, started_at timestamptz
);
INSERT INTO platform_maintenance(singleton) VALUES(true);
