CREATE TABLE conversation_reference_versions (
    conversation_id uuid PRIMARY KEY REFERENCES conversations(id) ON DELETE CASCADE,
    revision integer NOT NULL DEFAULT 0 CHECK (revision>=0)
);
CREATE TABLE conversation_references (
    conversation_id uuid NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
    source_id uuid NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
    message_ids uuid[] NOT NULL,
    digest text NOT NULL,
    PRIMARY KEY(conversation_id,source_id),
    CHECK(conversation_id<>source_id),
    CHECK(cardinality(message_ids) BETWEEN 1 AND 20)
);
CREATE INDEX conversation_references_source ON conversation_references(source_id);
CREATE TABLE conversation_reference_runs (
    run_id uuid PRIMARY KEY REFERENCES task_runs(id) ON DELETE CASCADE,
    revision integer NOT NULL,
    sources jsonb NOT NULL
);
