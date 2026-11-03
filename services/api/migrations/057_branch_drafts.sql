ALTER TABLE conversation_branches ADD COLUMN draft_text text NOT NULL DEFAULT '' CHECK(length(draft_text)<=16000);
