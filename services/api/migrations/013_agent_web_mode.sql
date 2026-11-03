ALTER TABLE agents ADD COLUMN web_mode text NOT NULL DEFAULT 'tavily'
    CHECK(web_mode IN ('tavily','openai_native'));
