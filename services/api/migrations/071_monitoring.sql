CREATE TABLE monitor_samples (
 sampled_at timestamptz PRIMARY KEY, snapshot jsonb NOT NULL
);
CREATE TABLE monitor_rules (
 id uuid PRIMARY KEY, owner_id uuid NOT NULL REFERENCES users(id), name text NOT NULL,
 scope text NOT NULL CHECK(scope IN ('system','personal')), metric text NOT NULL,
 target text NOT NULL DEFAULT '', threshold numeric NOT NULL, duration_seconds integer NOT NULL DEFAULT 0,
 cooldown_seconds integer NOT NULL DEFAULT 300, enabled boolean NOT NULL DEFAULT true,
 revision integer NOT NULL DEFAULT 1, channel_ids uuid[] NOT NULL DEFAULT '{}',
 pending_since timestamptz, last_notified_at timestamptz,
 created_at timestamptz NOT NULL DEFAULT now(), updated_at timestamptz NOT NULL DEFAULT now(),
 CHECK(duration_seconds BETWEEN 0 AND 86400), CHECK(cooldown_seconds BETWEEN 30 AND 604800)
);
CREATE TABLE monitor_alerts (
 id uuid PRIMARY KEY, rule_id uuid NOT NULL REFERENCES monitor_rules(id), owner_id uuid NOT NULL REFERENCES users(id),
 rule_revision integer NOT NULL, name text NOT NULL, scope text NOT NULL,
 status text NOT NULL CHECK(status IN ('FIRING','RECOVERED')), value numeric,
 first_seen_at timestamptz NOT NULL, last_seen_at timestamptz NOT NULL,
 acknowledged_at timestamptz, acknowledged_by uuid REFERENCES users(id), recovered_at timestamptz
);
CREATE UNIQUE INDEX monitor_one_firing ON monitor_alerts(rule_id) WHERE status='FIRING';
CREATE INDEX monitor_alert_history ON monitor_alerts(owner_id,first_seen_at DESC,id DESC);
ALTER TABLE notifications DROP CONSTRAINT notifications_target_kind_check;
ALTER TABLE notifications ADD CHECK(target_kind IN ('chat','work','automation','batch','monitoring'));
