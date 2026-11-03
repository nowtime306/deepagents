ALTER TABLE agents ADD COLUMN web_enabled boolean NOT NULL DEFAULT false;
CREATE TABLE search_settings (
    singleton boolean PRIMARY KEY DEFAULT true CHECK(singleton),
    api_key_ciphertext text NOT NULL DEFAULT '',
    enabled boolean NOT NULL DEFAULT false
);
