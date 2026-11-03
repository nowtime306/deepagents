# Fixed readonly Workflow engineering cases

This increment implements AET-F02 and AET-F04 from the unchanged A20 catalog. It does
not demonstrate the remaining tasks, UI behavior, production release or model quality.
The readonly published Workflow endpoint executes synchronously over formal HTTP and
persists workflow_runs/node events. It is not a durable queue consumer. Work cases use
their own queue and evidence adapter; these entry points are not interchanged.

F02 publishes the fixed start → upper → choose(contains HELLO) → yes/no graph. The
case submits hello to the actual fixture publication version. Facts verify the input,
output HELLO and complete ordered started/completed events for start, upper, choose,
yes, including each node's actual input/output digests. No started/completed no branch
is accepted. Correct final output alone is insufficient. Missing event observations
block acceptance; an observed wrong branch is a definite factual FAIL.

F04 publishes the fixed object input schema with required string name/maxLength12.
The transport input is JSON text. After the required legal initialization test and
publication, the runner seals its initialization and side-effect baseline. It then
submits `{"name":4}` with a durable request intent and client request UUID, requiring
HTTP422 / WORKFLOW_INPUT_INVALID and the named field indication. A formal filtered
run collection must show no run for that UUID, and the whole-owner effect projection
must equal the sealed baseline. This proof is sealed before dispatching the legal
`{"name":"李四"}` contrast to the same publication. Final observations must add exactly
that legal run and its start/end events. The report explicitly retains REJECTED with
no platform root for the bad request, and SUCCEEDED for the legal contrast. The case
assertion passing does not turn the rejected business request into a successful run.
An ambiguous response becomes UNKNOWN/NEEDS_REVIEW and is never automatically posted
again. A definite observed unwanted effect is retained as FAIL and stops new dispatch.

Initialization itself creates one real test run. Its ID, node events and native usage
are archived separately within the case evidence, and included in the case usage
total; it is not another case or repeat. The publication ID, business digest, graph
digest and real version are bound to both initialization and main observations.
Baseline/rejection/final files use the same identity-bound relative-path/hash store
and durable reference mechanism as the reviewed A02 evidence contract. All previous
references remain checked. Offline report recomputes facts, usage and business outcomes.
Cleanup re-observes the fixed owner effect projection and requires it still equal the
final retained projection. Equality alone is insufficient: active or unknown runs,
operations/model usage, pending/unknown provider work and unaccounted sandbox creation
reservations prevent CLEAN. Complete known terminal business failures remain disposable;
the cleanup gate does not require the business facts to PASS. Offline CLEAN claims run
the same retained cleanup gate, including the W01 artifact/resource evidence gate.
Missing/changed facts cannot justify cleanup or a valid PASS.
Persistence failure poisons the attempt for manual review; an in-memory reference cannot
authorize a later grade, cleanup or new dispatch. Binding/repeat/version checks retain
JSON type distinctions, including boolean versus integer, for the shared A02 path too.

The target-only `/api/v1/agent-evaluations/profiles/workflows/{id}/observations` route
first attests the environment/owner and only accepts a workflow whose default model
has an immutable F02/F04 lead binding for that owner. A single SQL statement obtains
the owned workflow's complete run-ID set and the owner's workflow/node/Ask/Work/model
usage/operation/sandbox counters, including active and unknown states. It exposes no
SQL, arbitrary path, source text or logs. Controlled provider and sandbox admission
counters are explicit observed/complete proofs, not missing-root values interpreted
as zero. Their in-process locks/state and the DB statement are separate observation
boundaries, not a claimed atomic transaction spanning the filesystem/provider/DB.

For these two cases the controlled model factory enforces zero calls and zero tokens
before any generation, across roots and reconstruction. Native per-root usage must
be complete and known zero. Both initialization and task graphs have only the fixed
readonly transform/condition/start/end nodes. They create no Work task or sandbox;
the side-effect observer checks this rather than inferring it from a final answer.
This remains controlled-engineering coverage, not a general real-provider budget proof.

Audit requirements derive from the immutable reviewed case's metrics: F02/F04 have
no metrics and require execution/export only. They do not invent DeepEval imports or
measurements. A02 still declares its actual ToolCorrectness metric and requires its
execution/import/metric/export phases. The exact current audit-index and last export
session closure and final report-ready receipt remain mandatory for every case.
W01 initialization additionally freezes the attested sandbox namespace before any
fixture POST; its evidence and cleanup_ready adapter must independently match it.
W01/F02/F04 bind that namespace to subsequent transport attestations, including the
last attestation before each formal POST. Drift after initialization prevents new
submissions rather than being discovered only after execution.

Author HTTP-transport and API-venv engineering tests are separate from real acceptance.
After source freeze, independent review/QA and a fixed candidate are required before
new isolated targets execute these cases. External/paid providers remain disabled.
