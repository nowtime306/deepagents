ALTER TABLE model_configs ADD COLUMN supports_images boolean;
ALTER TABLE conversation_attachments ADD COLUMN media_type text NOT NULL DEFAULT 'application/octet-stream';
ALTER TABLE conversations ADD COLUMN native_search boolean NOT NULL DEFAULT true;
