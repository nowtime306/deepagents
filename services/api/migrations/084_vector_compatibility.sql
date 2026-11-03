-- No retrospective identity is invented for existing vectors. NULL means pending verification.
ALTER TABLE documents ADD COLUMN vector_fingerprint text;
ALTER TABLE documents ADD COLUMN vector_dimensions integer CHECK(vector_dimensions BETWEEN 1 AND 8192);
ALTER TABLE private_memories ADD COLUMN vector_fingerprint text;
ALTER TABLE private_memories ADD COLUMN vector_dimensions integer CHECK(vector_dimensions BETWEEN 1 AND 8192);
ALTER TABLE knowledge_image_indexes ADD COLUMN vector_fingerprint text;
ALTER TABLE knowledge_video_jobs ADD COLUMN vector_fingerprint text;

CREATE TABLE embedding_compatibility_evidence (
    model_id text NOT NULL,
    target_key text NOT NULL,
    source_key text NOT NULL,
    dimensions integer NOT NULL CHECK(dimensions BETWEEN 1 AND 8192),
    evidence_digest text NOT NULL,
    verified_by uuid NOT NULL,
    verified_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY(model_id,target_key)
);

CREATE TRIGGER configuration_history AFTER INSERT OR UPDATE OR DELETE
ON embedding_compatibility_evidence FOR EACH ROW EXECUTE FUNCTION
record_configuration_change('model','target_key,source_key,dimensions,evidence_digest');
