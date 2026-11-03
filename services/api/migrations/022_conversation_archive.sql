ALTER TABLE conversations ADD COLUMN archived boolean NOT NULL DEFAULT false;
CREATE INDEX conversations_owner_archive ON conversations(owner_id,archived,created_at DESC,id DESC);
CREATE INDEX messages_history_page ON conversation_messages(conversation_id,created_at DESC,id DESC);
