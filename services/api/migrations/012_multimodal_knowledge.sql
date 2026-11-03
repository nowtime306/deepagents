ALTER TABLE model_configs DROP CONSTRAINT model_configs_protocol_check;
ALTER TABLE model_configs ADD CONSTRAINT model_configs_protocol_check
    CHECK (protocol IN ('openai','anthropic','custom','dashscope_multimodal'));
CREATE TABLE knowledge_image_indexes (
    knowledge_id uuid PRIMARY KEY REFERENCES knowledge_bases(id) ON DELETE CASCADE,
    model_id text NOT NULL, model_name text NOT NULL, base_url text NOT NULL,
    dimensions integer CHECK(dimensions BETWEEN 1 AND 8192)
);
CREATE TABLE knowledge_images (
    id uuid PRIMARY KEY, knowledge_id uuid NOT NULL REFERENCES knowledge_bases(id) ON DELETE CASCADE,
    name text NOT NULL, media_type text NOT NULL, size integer NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX knowledge_images_knowledge_created ON knowledge_images(knowledge_id, created_at DESC);
