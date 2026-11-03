CREATE TABLE generated_images (
    id uuid PRIMARY KEY, owner_id uuid NOT NULL REFERENCES users(id),
    model_name text NOT NULL, prompt text NOT NULL, size integer NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX generated_images_owner_created ON generated_images(owner_id, created_at DESC);
