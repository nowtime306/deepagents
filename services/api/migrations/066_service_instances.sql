CREATE TABLE service_instances (
    id uuid PRIMARY KEY,
    kind text NOT NULL CHECK(kind IN ('api','ask','work')),
    process_id integer NOT NULL,
    backend_id integer NOT NULL,
    started_at timestamptz NOT NULL DEFAULT now(),
    heartbeat_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX service_instances_live ON service_instances(kind,heartbeat_at);
CREATE TABLE service_events (
    id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    instance_id uuid NOT NULL,
    kind text NOT NULL CHECK(kind IN ('api','ask','work')),
    event text NOT NULL CHECK(event IN ('started','stopped','fenced')),
    reason text,
    created_at timestamptz NOT NULL DEFAULT now()
);
