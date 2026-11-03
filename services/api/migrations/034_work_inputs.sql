CREATE TABLE work_input_files (
 id uuid PRIMARY KEY,task_id uuid NOT NULL REFERENCES work_tasks(id) ON DELETE CASCADE,
 owner_id uuid NOT NULL REFERENCES users(id),path text NOT NULL,size integer NOT NULL CHECK(size BETWEEN 0 AND 1048576),
 digest text NOT NULL,active boolean NOT NULL DEFAULT true,created_at timestamptz NOT NULL DEFAULT now()
);
CREATE UNIQUE INDEX work_input_path ON work_input_files(task_id,path) WHERE active;
ALTER TABLE work_queue_runs ADD COLUMN input_files jsonb NOT NULL DEFAULT '[]';
ALTER TABLE work_queue_runs ADD COLUMN restore_snapshot_id uuid REFERENCES work_snapshots(id) ON DELETE SET NULL;
