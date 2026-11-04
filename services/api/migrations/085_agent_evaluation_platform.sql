-- Agent evaluation metadata and durable admission queue; no worker or paid execution is enabled here.
CREATE TABLE agent_evaluation_datasets (
 id uuid PRIMARY KEY, owner_id uuid NOT NULL REFERENCES users(id) ON DELETE CASCADE,
 name text NOT NULL, description text NOT NULL DEFAULT '', revision integer NOT NULL DEFAULT 1 CHECK(revision>0),
 definition jsonb NOT NULL CHECK(jsonb_typeof(definition)='object'), definition_digest text NOT NULL,
 case_count integer NOT NULL CHECK(case_count BETWEEN 1 AND 100), mode_counts jsonb NOT NULL,
 current_version integer, created_at timestamptz NOT NULL DEFAULT now(), updated_at timestamptz NOT NULL DEFAULT now(),
 UNIQUE(id,owner_id)
);
CREATE TABLE agent_evaluation_dataset_versions (
 id uuid PRIMARY KEY, dataset_id uuid NOT NULL, owner_id uuid NOT NULL,
 version integer NOT NULL CHECK(version>0), definition jsonb NOT NULL, definition_digest text NOT NULL,
 case_count integer NOT NULL, mode_counts jsonb NOT NULL, notes text NOT NULL DEFAULT '',
 semantic_review_status text NOT NULL DEFAULT 'PENDING' CHECK(semantic_review_status IN ('PENDING','CONFIRMED')),
 created_at timestamptz NOT NULL DEFAULT now(), created_by uuid NOT NULL REFERENCES users(id),
 FOREIGN KEY(dataset_id,owner_id) REFERENCES agent_evaluation_datasets(id,owner_id) ON DELETE CASCADE,
 UNIQUE(dataset_id,owner_id,version), UNIQUE(dataset_id,owner_id,definition_digest), UNIQUE(id,dataset_id,owner_id)
);
CREATE TABLE agent_evaluation_version_sets (
 id uuid PRIMARY KEY, owner_id uuid NOT NULL REFERENCES users(id) ON DELETE CASCADE,
 name text NOT NULL, candidate_ref uuid NOT NULL, snapshot jsonb NOT NULL,
 created_at timestamptz NOT NULL DEFAULT now(), UNIQUE(id,owner_id)
);
CREATE TABLE agent_evaluation_runs (
 id uuid PRIMARY KEY, owner_id uuid NOT NULL REFERENCES users(id) ON DELETE CASCADE,
 dataset_id uuid NOT NULL, dataset_version_id uuid NOT NULL, version integer NOT NULL,
 request_id uuid NOT NULL, payload_digest text NOT NULL,
 execution_mode text NOT NULL CHECK(execution_mode IN ('engineering','effect')),
 repeats integer NOT NULL CHECK(repeats IN (1,3)), acceptance_target text NOT NULL CHECK(acceptance_target IN ('engineering','b100')),
 status text NOT NULL DEFAULT 'QUEUED' CHECK(status IN ('QUEUED','RUNNING','CANCEL_REQUESTED','COMPLETED','CANCELLED','FAILED','NEEDS_REVIEW')),
 gate_verdict text NOT NULL DEFAULT 'PENDING' CHECK(gate_verdict IN ('PENDING','PASS','FAIL','NEEDS_REVIEW')),
 revision integer NOT NULL DEFAULT 1, snapshot jsonb NOT NULL, budgets jsonb NOT NULL,
 unique_task_count integer NOT NULL, total_samples integer NOT NULL, completed_samples integer NOT NULL DEFAULT 0,
 counts jsonb NOT NULL DEFAULT '{"pass":0,"fail":0,"needs_review":0,"not_run":0}',
 cleanup_status text NOT NULL DEFAULT 'NOT_STARTED', cancel_requested boolean NOT NULL DEFAULT false,
 dispatch_started boolean NOT NULL DEFAULT false, lease_token uuid, lease_until timestamptz, worker_id uuid,
 errors jsonb NOT NULL DEFAULT '[]', created_at timestamptz NOT NULL DEFAULT now(), started_at timestamptz, finished_at timestamptz,
 FOREIGN KEY(dataset_version_id,dataset_id,owner_id) REFERENCES agent_evaluation_dataset_versions(id,dataset_id,owner_id),
 UNIQUE(owner_id,request_id), UNIQUE(id,owner_id)
);
CREATE INDEX agent_evaluation_claim ON agent_evaluation_runs(created_at,id) WHERE status='QUEUED';
CREATE TABLE agent_evaluation_samples (
 id uuid PRIMARY KEY, owner_id uuid NOT NULL, run_id uuid NOT NULL,
 case_id text NOT NULL, case_name text NOT NULL, mode text NOT NULL CHECK(mode IN ('ask','work','workflow')),
 repeat_index integer NOT NULL CHECK(repeat_index BETWEEN 1 AND 3), case_snapshot jsonb NOT NULL,
 status text NOT NULL DEFAULT 'PENDING', task_outcome text NOT NULL DEFAULT 'NOT_RUN', metric_status text NOT NULL DEFAULT 'NOT_RUN',
 gate_verdict text NOT NULL DEFAULT 'PENDING', cleanup_status text NOT NULL DEFAULT 'NOT_STARTED',
 facts jsonb NOT NULL DEFAULT '[]', metrics jsonb NOT NULL DEFAULT '[]', evidence_refs jsonb NOT NULL DEFAULT '[]',
 root_refs jsonb NOT NULL DEFAULT '[]', configuration_refs jsonb NOT NULL DEFAULT '[]', usage jsonb,
 timings jsonb NOT NULL DEFAULT '{}', errors jsonb NOT NULL DEFAULT '[]', revision integer NOT NULL DEFAULT 1,
 created_at timestamptz NOT NULL DEFAULT now(), started_at timestamptz, finished_at timestamptz,
 FOREIGN KEY(run_id,owner_id) REFERENCES agent_evaluation_runs(id,owner_id) ON DELETE CASCADE,
 UNIQUE(run_id,case_id,repeat_index), UNIQUE(id,owner_id)
);
CREATE TABLE agent_evaluation_reports (
 id uuid PRIMARY KEY, owner_id uuid NOT NULL, run_id uuid NOT NULL, manifest jsonb NOT NULL,
 files jsonb NOT NULL, verified boolean NOT NULL DEFAULT false, redacted boolean NOT NULL DEFAULT false,
 created_at timestamptz NOT NULL DEFAULT now(),
 FOREIGN KEY(run_id,owner_id) REFERENCES agent_evaluation_runs(id,owner_id) ON DELETE CASCADE,
 UNIQUE(run_id,owner_id)
);
CREATE TABLE agent_evaluation_comparisons (
 id uuid PRIMARY KEY, owner_id uuid NOT NULL REFERENCES users(id) ON DELETE CASCADE, baseline_run_id uuid NOT NULL, candidate_run_id uuid NOT NULL,
 changed_axis text NOT NULL, comparable boolean NOT NULL, reasons jsonb NOT NULL, summary jsonb,
 case_changes jsonb NOT NULL DEFAULT '[]', created_at timestamptz NOT NULL DEFAULT now(),
 FOREIGN KEY(baseline_run_id,owner_id) REFERENCES agent_evaluation_runs(id,owner_id),
 FOREIGN KEY(candidate_run_id,owner_id) REFERENCES agent_evaluation_runs(id,owner_id), UNIQUE(id,owner_id)
);
CREATE TABLE agent_evaluation_failure_candidates (
 id uuid PRIMARY KEY, owner_id uuid NOT NULL REFERENCES users(id) ON DELETE CASCADE, source_sample_id uuid,
 name text NOT NULL, status text NOT NULL DEFAULT 'PENDING_REVIEW' CHECK(status IN ('PENDING_REVIEW','APPROVED','REJECTED')),
 revision integer NOT NULL DEFAULT 1, redacted_material jsonb NOT NULL, material_digest text NOT NULL,
 authorization_note text NOT NULL, created_at timestamptz NOT NULL DEFAULT now(),
 FOREIGN KEY(source_sample_id,owner_id) REFERENCES agent_evaluation_samples(id,owner_id), UNIQUE(id,owner_id)
);
CREATE TABLE agent_evaluation_reviews (
 id uuid PRIMARY KEY, owner_id uuid NOT NULL REFERENCES users(id) ON DELETE CASCADE,
 target_kind text NOT NULL CHECK(target_kind IN ('sample','failure_candidate')), target_id uuid NOT NULL,
 reviewer_id uuid NOT NULL REFERENCES users(id), decision text NOT NULL, reason text NOT NULL, rules_version text NOT NULL DEFAULT '',
 original_gate text, created_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX agent_evaluation_reviews_owner ON agent_evaluation_reviews(owner_id,target_kind,target_id,created_at);
CREATE TABLE agent_evaluation_events (
 id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY, owner_id uuid NOT NULL, run_id uuid NOT NULL,
 event text NOT NULL, data jsonb NOT NULL DEFAULT '{}', created_at timestamptz NOT NULL DEFAULT now(),
 FOREIGN KEY(run_id,owner_id) REFERENCES agent_evaluation_runs(id,owner_id) ON DELETE CASCADE
);
