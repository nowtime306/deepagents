CREATE TABLE speech_outputs (
    id uuid PRIMARY KEY, owner_id uuid NOT NULL REFERENCES users(id),
    model_name text NOT NULL, voice text NOT NULL, input text NOT NULL,
    size integer NOT NULL, created_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX speech_outputs_owner_created ON speech_outputs(owner_id, created_at DESC);
