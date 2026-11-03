CREATE TABLE operation_executions (
 id uuid PRIMARY KEY,run_id uuid NOT NULL REFERENCES work_queue_runs(id) ON DELETE CASCADE,
 approval_id uuid NOT NULL REFERENCES work_approvals(id) ON DELETE CASCADE,
 action_index integer NOT NULL,name text NOT NULL,args jsonb NOT NULL,digest text NOT NULL,
 status text NOT NULL DEFAULT 'APPROVED' CHECK(status IN ('APPROVED','STARTED','SUCCEEDED','FAILED','UNKNOWN')),
 lease_token uuid,exit_code integer,output text,created_at timestamptz NOT NULL DEFAULT now(),
 started_at timestamptz,finished_at timestamptz,UNIQUE(approval_id,action_index)
);
CREATE INDEX operation_run ON operation_executions(run_id,created_at,id);
