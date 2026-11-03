CREATE TABLE knowledge_quality_policies (
 knowledge_id uuid PRIMARY KEY REFERENCES knowledge_bases(id) ON DELETE CASCADE,
 owner_id uuid NOT NULL REFERENCES users(id), enforce_publish boolean NOT NULL DEFAULT false,
 minimum_questions integer NOT NULL DEFAULT 50 CHECK(minimum_questions BETWEEN 1 AND 50),
 recall_target numeric NOT NULL DEFAULT .9 CHECK(recall_target BETWEEN 0 AND 1),
 citation_target numeric NOT NULL DEFAULT .95 CHECK(citation_target BETWEEN 0 AND 1),
 no_answer_target numeric NOT NULL DEFAULT .9 CHECK(no_answer_target BETWEEN 0 AND 1),
 revision integer NOT NULL DEFAULT 1, updated_at timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE knowledge_quality_reviews (
 experiment_id uuid NOT NULL REFERENCES knowledge_experiments(id) ON DELETE CASCADE,
 question_id text NOT NULL, owner_id uuid NOT NULL REFERENCES users(id),
 answer text NOT NULL, cited_documents uuid[] NOT NULL DEFAULT '{}',
 citation_correct boolean, refusal_correct boolean, note text NOT NULL DEFAULT '',
 revision integer NOT NULL DEFAULT 1, updated_at timestamptz NOT NULL DEFAULT now(),
 PRIMARY KEY(experiment_id,question_id)
);
