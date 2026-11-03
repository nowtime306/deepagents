ALTER TABLE work_queue_runs ADD COLUMN next_event_id bigint NOT NULL DEFAULT 0;
ALTER TABLE work_queue_runs ADD COLUMN error_code text;
ALTER TABLE work_queue_runs ADD COLUMN result jsonb;
CREATE TABLE work_run_events (
    run_id uuid NOT NULL REFERENCES work_queue_runs(id) ON DELETE CASCADE,
    event_id bigint NOT NULL,
    event text NOT NULL,
    data jsonb NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY(run_id,event_id)
);
