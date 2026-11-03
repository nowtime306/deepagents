CREATE TABLE run_resource_stages (
    owner_id uuid NOT NULL,
    mode text NOT NULL,
    run_id uuid NOT NULL,
    phase integer NOT NULL CHECK (phase > 0),
    created_at timestamptz NOT NULL DEFAULT now(),
    confirmed_by uuid,
    reason text NOT NULL DEFAULT 'initial',
    safe_changes jsonb NOT NULL DEFAULT '{}',
    PRIMARY KEY (owner_id,mode,run_id,phase)
);
CREATE TABLE run_resource_snapshots (
    snapshot_id uuid NOT NULL UNIQUE DEFAULT gen_random_uuid(),
    owner_id uuid NOT NULL,
    mode text NOT NULL,
    run_id uuid NOT NULL,
    phase integer NOT NULL DEFAULT 1 CHECK (phase > 0),
    resource_kind text NOT NULL,
    resource_id text NOT NULL,
    revision text NOT NULL,
    resolved_at timestamptz NOT NULL DEFAULT now(),
    used_at timestamptz,
    snapshot jsonb NOT NULL,
    execution_config text NOT NULL,
    credential_scope text,
    credential_changed boolean NOT NULL DEFAULT false,
    PRIMARY KEY (owner_id,mode,run_id,phase,resource_kind,resource_id)
);
ALTER TABLE model_usage ADD COLUMN resource_snapshot_id uuid REFERENCES run_resource_snapshots(snapshot_id);
ALTER TABLE model_usage ADD COLUMN resource_phase integer;
ALTER TABLE model_usage ADD COLUMN effective_parameters jsonb;
ALTER TABLE work_queue_runs ADD COLUMN resource_resume_pending boolean NOT NULL DEFAULT false;

-- Old approval receipts cannot prove the model/tool/Skill content now mounted by the runtime.
-- Preserve the receipts, checkpoint and workspace for manual review; never promote current content to history.
CREATE FUNCTION rdc_083_has_checkpoint(run uuid) RETURNS boolean LANGUAGE plpgsql AS $$
BEGIN
    -- A fresh install creates checkpoint tables only after the SQL migrations commit.
    IF to_regclass('checkpoints') IS NULL THEN RETURN false; END IF;
    RETURN EXISTS (SELECT 1 FROM checkpoints WHERE thread_id='work:'||run::text);
END $$;
WITH held AS (
    UPDATE work_queue_runs r SET status='NEEDS_REVIEW',error_code='RESOURCE_HISTORY_MISSING',
        lease_token=NULL,lease_until=NULL,updated_at=now()
    WHERE NOT EXISTS (SELECT 1 FROM run_resource_snapshots s WHERE s.owner_id=r.owner_id
        AND s.mode='work' AND s.run_id=r.id)
    AND (r.status IN ('WAITING_APPROVAL','PAUSED') OR (r.status='PENDING' AND (
        EXISTS (SELECT 1 FROM work_approvals a WHERE a.run_id=r.id) OR
        EXISTS (SELECT 1 FROM operation_executions o WHERE o.run_id=r.id) OR
        EXISTS (SELECT 1 FROM model_usage u WHERE u.run_id=r.id AND u.owner_id=r.owner_id AND u.mode='work') OR
        rdc_083_has_checkpoint(r.id))))
    RETURNING r.id
), retained AS (
    UPDATE work_sandbox_sessions s SET retention_required=true
    WHERE s.state<>'RELEASED' AND EXISTS (SELECT 1 FROM held h WHERE h.id=s.run_id)
    RETURNING s.id
)
UPDATE workflow_runs w SET status='NEEDS_REVIEW',finished_at=now()
WHERE w.status='RUNNING' AND (
    EXISTS (SELECT 1 FROM work_run_workflows b JOIN held h ON h.id=b.run_id WHERE b.workflow_run_id=w.id)
    OR EXISTS (SELECT 1 FROM external_workflow_jobs j JOIN api_credentials k ON k.id=j.credential_id
        JOIN held h ON h.id=k.parent_work_run WHERE j.run_id=w.id)
);
DROP FUNCTION rdc_083_has_checkpoint(uuid);
