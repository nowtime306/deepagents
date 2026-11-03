CREATE TABLE work_task_workflows (
    task_id uuid PRIMARY KEY REFERENCES work_tasks(id) ON DELETE CASCADE,
    workflow_id uuid REFERENCES workflows(id) ON DELETE SET NULL
);
