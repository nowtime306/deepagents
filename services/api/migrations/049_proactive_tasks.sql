CREATE TABLE automations (
    id uuid PRIMARY KEY,
    owner_id uuid NOT NULL REFERENCES users(id),
    name text NOT NULL,
    agent_version_id uuid REFERENCES agent_versions(id) ON DELETE SET NULL,
    snapshot jsonb NOT NULL,
    mode text NOT NULL CHECK(mode IN ('ask','work')),
    kind text NOT NULL CHECK(kind IN ('once','interval','cron','webhook')),
    goal text NOT NULL,
    timezone text NOT NULL DEFAULT 'Asia/Shanghai',
    starts_at timestamptz NOT NULL,
    interval_seconds integer,
    cron text NOT NULL DEFAULT '',
    next_due_at timestamptz,
    enabled boolean NOT NULL DEFAULT false,
    paused_at timestamptz DEFAULT now(),
    completed_at timestamptz,
    deleted_at timestamptz,
    missed_policy text NOT NULL DEFAULT 'skip' CHECK(missed_policy IN ('skip','once')),
    overlap_policy text NOT NULL DEFAULT 'skip' CHECK(overlap_policy IN ('skip','queue')),
    budget jsonb NOT NULL,
    environment text NOT NULL DEFAULT 'coding' CHECK(environment IN ('coding','browser')),
    network_domains jsonb NOT NULL DEFAULT '[]',
    notify_channel_ids jsonb NOT NULL DEFAULT '[]',
    webhook_key_ciphertext text NOT NULL DEFAULT '',
    webhook_hourly_limit integer NOT NULL DEFAULT 60 CHECK(webhook_hourly_limit BETWEEN 1 AND 1000),
    revision integer NOT NULL DEFAULT 1,
    last_error text,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX automations_due ON automations(next_due_at) WHERE enabled AND deleted_at IS NULL;
CREATE INDEX automations_owner ON automations(owner_id,created_at);

CREATE TABLE automation_occurrences (
    id uuid PRIMARY KEY,
    automation_id uuid NOT NULL REFERENCES automations(id),
    owner_id uuid NOT NULL REFERENCES users(id),
    scheduled_for timestamptz NOT NULL,
    external_event_id text,
    input_digest text,
    title text NOT NULL DEFAULT '',
    notify_pins jsonb NOT NULL DEFAULT '[]',
    status text NOT NULL CHECK(status IN ('DISPATCHED','SKIPPED_MISSED','SKIPPED_BUSY','REJECTED')),
    ask_run_id uuid REFERENCES task_runs(id) ON DELETE SET NULL,
    work_run_id uuid REFERENCES work_queue_runs(id) ON DELETE SET NULL,
    root_run_id uuid,
    target_id uuid,
    result_status text,
    error_code text,
    notified boolean NOT NULL DEFAULT false,
    created_at timestamptz NOT NULL DEFAULT now(),
    finished_at timestamptz,
    UNIQUE(automation_id,scheduled_for),
    UNIQUE(automation_id,external_event_id)
);

CREATE TABLE agent_batches (
    id uuid PRIMARY KEY,
    owner_id uuid NOT NULL REFERENCES users(id),
    name text NOT NULL,
    agent_version_id uuid REFERENCES agent_versions(id) ON DELETE SET NULL,
    snapshot jsonb NOT NULL,
    mode text NOT NULL CHECK(mode IN ('ask','work')),
    status text NOT NULL DEFAULT 'ACTIVE' CHECK(status IN ('ACTIVE','PAUSED','COMPLETED','CANCELLED')),
    max_parallel integer NOT NULL DEFAULT 2 CHECK(max_parallel BETWEEN 1 AND 2),
    on_failure text NOT NULL DEFAULT 'pause' CHECK(on_failure IN ('pause','continue')),
    model_call_limit integer NOT NULL CHECK(model_call_limit BETWEEN 1 AND 800),
    token_limit integer NOT NULL CHECK(token_limit BETWEEN 1024 AND 1600000),
    item_budget jsonb NOT NULL,
    environment text NOT NULL DEFAULT 'coding' CHECK(environment IN ('coding','browser')),
    network_domains jsonb NOT NULL DEFAULT '[]',
    notify_channel_ids jsonb NOT NULL DEFAULT '[]',
    notify_pins jsonb NOT NULL DEFAULT '[]',
    last_error text,
    revision integer NOT NULL DEFAULT 1,
    deleted_at timestamptz,
    notified boolean NOT NULL DEFAULT false,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE batch_items (
    id uuid PRIMARY KEY,
    batch_id uuid NOT NULL REFERENCES agent_batches(id),
    position integer NOT NULL,
    goal text NOT NULL,
    status text NOT NULL DEFAULT 'WAITING' CHECK(status IN ('WAITING','RUNNING','SUCCEEDED','FAILED','CANCELLED','UNKNOWN')),
    ask_run_id uuid REFERENCES task_runs(id) ON DELETE SET NULL,
    work_run_id uuid REFERENCES work_queue_runs(id) ON DELETE SET NULL,
    root_run_id uuid,
    target_id uuid,
    error_code text,
    acknowledged boolean NOT NULL DEFAULT false,
    created_at timestamptz NOT NULL DEFAULT now(),
    finished_at timestamptz,
    UNIQUE(batch_id,position)
);
CREATE INDEX batches_active ON agent_batches(created_at) WHERE status='ACTIVE' AND deleted_at IS NULL;
CREATE INDEX batch_items_batch ON batch_items(batch_id,status,position);

ALTER TABLE task_runs ADD COLUMN origin text NOT NULL DEFAULT 'interactive' CHECK(origin IN ('interactive','schedule','batch'));
ALTER TABLE task_runs ADD COLUMN automation_occurrence_id uuid REFERENCES automation_occurrences(id) ON DELETE SET NULL;
ALTER TABLE task_runs ADD COLUMN batch_item_id uuid REFERENCES batch_items(id) ON DELETE SET NULL;
ALTER TABLE work_queue_runs ADD COLUMN origin text NOT NULL DEFAULT 'interactive' CHECK(origin IN ('interactive','schedule','batch'));
ALTER TABLE work_queue_runs ADD COLUMN automation_occurrence_id uuid REFERENCES automation_occurrences(id) ON DELETE SET NULL;
ALTER TABLE work_queue_runs ADD COLUMN batch_item_id uuid REFERENCES batch_items(id) ON DELETE SET NULL;

CREATE FUNCTION record_automation_result() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE outcome text;
BEGIN
    IF NEW.status=OLD.status OR NEW.origin='interactive' THEN RETURN NEW; END IF;
    outcome := CASE
        WHEN NEW.status IN ('SUCCEEDED','COMPLETED') THEN 'SUCCEEDED'
        WHEN NEW.status='CANCELLED' THEN 'CANCELLED'
        WHEN NEW.status='NEEDS_REVIEW' OR (NEW.status='FAILED' AND
            (coalesce(NEW.error_code,'') LIKE '%UNKNOWN%' OR NEW.error_code IN ('WORKER_STOPPED','EXECUTION_UNCERTAIN')
             OR EXISTS(SELECT 1 FROM model_usage WHERE run_id=NEW.id AND status IN ('PENDING','UNKNOWN'))))
            THEN 'UNKNOWN'
        WHEN NEW.status='FAILED' THEN 'FAILED'
        ELSE NULL END;
    IF outcome IS NULL THEN RETURN NEW; END IF;
    IF NEW.automation_occurrence_id IS NOT NULL THEN
        UPDATE automation_occurrences SET result_status=outcome,error_code=NEW.error_code,finished_at=now()
        WHERE id=NEW.automation_occurrence_id;
    END IF;
    IF NEW.batch_item_id IS NOT NULL THEN
        UPDATE batch_items SET status=outcome,error_code=NEW.error_code,finished_at=now()
        WHERE id=NEW.batch_item_id AND status='RUNNING';
    END IF;
    RETURN NEW;
END;
$$;
CREATE TRIGGER task_runs_automation_result AFTER UPDATE OF status ON task_runs
FOR EACH ROW EXECUTE FUNCTION record_automation_result();
CREATE TRIGGER work_runs_automation_result AFTER UPDATE OF status ON work_queue_runs
FOR EACH ROW EXECUTE FUNCTION record_automation_result();

CREATE TABLE notifications (
    id uuid PRIMARY KEY,
    owner_id uuid NOT NULL REFERENCES users(id),
    title text NOT NULL,
    message text NOT NULL,
    target_kind text NOT NULL CHECK(target_kind IN ('chat','work','automation','batch')),
    target_id uuid,
    dedupe_key text NOT NULL,
    read_at timestamptz,
    created_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE(owner_id,dedupe_key)
);
CREATE INDEX notifications_owner ON notifications(owner_id,created_at DESC);
CREATE TABLE notification_channels (
    id uuid PRIMARY KEY,
    owner_id uuid NOT NULL REFERENCES users(id),
    name text NOT NULL,
    kind text NOT NULL CHECK(kind IN ('webhook','feishu','wecom','slack')),
    endpoint_ciphertext text NOT NULL,
    secret_ciphertext text NOT NULL DEFAULT '',
    host_hint text NOT NULL,
    allow_private boolean NOT NULL DEFAULT false,
    enabled boolean NOT NULL DEFAULT true,
    revision integer NOT NULL DEFAULT 1,
    created_at timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE notification_deliveries (
    id uuid PRIMARY KEY,
    notification_id uuid NOT NULL REFERENCES notifications(id),
    channel_id uuid REFERENCES notification_channels(id) ON DELETE SET NULL,
    channel_revision integer NOT NULL,
    status text NOT NULL DEFAULT 'PENDING' CHECK(status IN ('PENDING','SENDING','SUCCEEDED','FAILED','UNKNOWN','CANCELLED')),
    lease_token uuid,
    lease_until timestamptz,
    attempts integer NOT NULL DEFAULT 0,
    error_code text,
    created_at timestamptz NOT NULL DEFAULT now(),
    finished_at timestamptz,
    UNIQUE(notification_id,channel_id)
);
CREATE INDEX notification_deliveries_pending ON notification_deliveries(created_at) WHERE status IN ('PENDING','SENDING');

UPDATE platform_roles SET permissions=permissions || '["automation.read","automation.manage","automation.execute","notifications.read","notifications.manage"]'::jsonb WHERE id='member';
UPDATE platform_roles SET permissions=permissions || '["automation.read","notifications.read"]'::jsonb WHERE id='viewer';
