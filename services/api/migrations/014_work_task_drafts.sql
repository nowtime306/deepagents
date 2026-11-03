CREATE TABLE work_tasks (
    id uuid PRIMARY KEY, owner_id uuid NOT NULL REFERENCES users(id),
    title text NOT NULL, goal text NOT NULL, status text NOT NULL DEFAULT 'DRAFT' CHECK(status IN ('DRAFT')),
    revision integer NOT NULL DEFAULT 1, created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX work_tasks_owner_updated ON work_tasks(owner_id,updated_at DESC);
