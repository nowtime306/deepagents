ALTER TABLE knowledge_bases ADD COLUMN keyword_min_coverage double precision NOT NULL DEFAULT 0
    CHECK (keyword_min_coverage >= 0 AND keyword_min_coverage <= 1);
