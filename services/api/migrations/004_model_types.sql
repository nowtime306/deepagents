ALTER TABLE model_configs DROP CONSTRAINT model_configs_kind_check;
ALTER TABLE model_configs ADD CONSTRAINT model_configs_kind_check CHECK (
    kind IN ('chat','embedding','multimodal_embedding','vision','image_generation',
             'video','audio','rerank','speech_to_text','text_to_speech')
);
ALTER TABLE model_configs ADD COLUMN provider text NOT NULL DEFAULT 'custom';
ALTER TABLE model_configs ADD COLUMN protocol text NOT NULL DEFAULT 'openai'
    CHECK (protocol IN ('openai','anthropic','custom'));
ALTER TABLE model_configs ADD CONSTRAINT model_default_chat_only
    CHECK (NOT is_default OR (kind='chat' AND protocol IN ('openai','anthropic')));
