ALTER TABLE work_approvals DROP CONSTRAINT work_approvals_decision_check;
ALTER TABLE work_approvals ADD CONSTRAINT work_approvals_decision_check CHECK(decision IN ('approve','reject','edit'));
ALTER TABLE work_approvals ADD COLUMN edited_actions jsonb;
