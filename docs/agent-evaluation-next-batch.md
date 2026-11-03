# Next evaluation increment: A01 automation lifecycle

Baseline: `780a376520b9cd26badcc3c5c31547f0b0a1b5cb`. This increment first implements
one A01 case/one repeat. Existing A02/W01/F02/F04 inputs, outcomes and retained receipts
remain unchanged. Author checks and independent review/QA precede any new real task;
an author HTTP-transport test is not business acceptance or production release.

## Required execution

1. Create a sealed controlled A01 model and publish the fixed Agent v1 through formal
   APIs. Maintain the model from revision1 to revision2/temperature0.4 by a CAS update,
   without republishing the Agent. Keep the actual published version ID and old model
   revision as initialization evidence; never include credentials in evidence.
2. Submit the fixed stock message through the formal Ask queue. Its real tools must
   list available published Agents and create one paused automation draft using the
   actual returned version. A driver POST creating an automation cannot replace this.
3. Seal official run/messages/SSE events/configuration usage and the disabled draft
   with zero occurrences before enabling it. The native model calls must actually use
   model revision2, linked to a used runtime snapshot; resolved-only metadata is insufficient.
4. Explicitly enable the registered draft with the formal automation state API. Then
   invoke one target-only fixed scheduler action, calling the real existing scheduler
   for that one owned once schedule. No global tick, arbitrary SQL, script, host time
   override, notification channel or production automation is permitted.
5. The real AskWorker must consume the scheduler-created occurrence. A target-specific
   worker wrapper determines its phase from the genuine claimed origin/occurrence/owner
   rows before delegating unchanged execution to the real Worker. The controlled model
   produces the fixed fictional daily summary, not another automation draft. Record the
   occurrence's real root/conversation/Agent version and terminal outcome through formal
   APIs. One created draft alone is not A01 acceptance.

The public resource snapshot's `revision` is its original TEXT reference (`"2"` for
this fixed maintenance), while each actual `model_usage.model_revision` remains the
integer `2`. Retain both original types and cross-check only after each strict contract
passes. Booleans, numeric snapshot revisions, `"02"`, `"2.0"` or text native revisions
are invalid; no text-to-integer coercion is permitted. The first 4627 A01 target stopped
before enable/dispatch because the earlier client incorrectly expected an integer
snapshot reference. Its original ERROR audit, retained draft and stopped database remain
unchanged; a new candidate must use a fresh target for actual acceptance.

## Budget and isolation

The published target factory already enforces per-root A01 limits4 calls/60 controlled
tokens. Add a durable owner+case+environment admission bound8 calls/120 tokens and at
most two actual provider roots, covering both the original Ask and the scheduled Ask.
The pre-effect check shares the existing locked persistent root reservations; unknown
or interrupted pending work poisons further case dispatch and reconstruction cannot
reset spend. No extra unmetered root, initializer generation, summarizer or retry is
authorized. Expected actual usage is main3 calls/45 tokens plus occurrence1/15, total4/60;
the conservative ceiling8/120 fits the unchanged stock10 calls/20,000 tokens budget.

The schedule explicitly declares4 calls/10,000 tokens, within the stock20,000-token
authorization; it must not inherit a larger caller budget by omission. This also leaves
room for the platform's input/output preflight reservation; the independent controlled
case cap still strictly limits actual known work to120 tokens. The capability proof
is target-only, registered by the actual enforcing profile code, and read-only readiness
must verify it before dispatch. A missing proof or unknown usage blocks further action.
No Work task, browser, sandbox or paid/external supplier is needed. Only a new isolated
target DB/owner/data/vault/namespace is eligible; no production settings or secrets are copied.

## Minimum trusted observations

Reuse formal Agent/version/model/Ask/messages/events/resource-trace/automation/occurrence
APIs. Add only a fixed target profile automation projection/action with authenticated
environment+owner+evaluation permission checks, sealed A01 model and actual source Ask
tool-created draft linkage. It accepts typed registered IDs, not SQL, paths or code.
The projection exposes bounded IDs, lifecycle flags, known native usage and actual
provider root counters/phase; it does not return prompts, arbitrary tool output, tokens
or hidden reasoning. Background broad automation scheduling stays disabled in this target;
the fixed action dispatches only its registered once schedule.

Every boundary observation is stored atomically under a safe relative path/hash before
grading or the next mutation: initialization, paused draft, enabled state, dispatched
occurrence and final facts. Bind owner/environment/candidate/catalog/case/rules/suite/
repeat/attempt/namespace. Recompute facts from retained bytes and verify all references
online and offline. Existing append-only Python command audits, last-export closure and
three-file ready receipt remain mandatory. A01 has no scoring metric and must not invent
DeepEval import/metric phases or claim a complete TaskCompletion trajectory.

## Failure and cleanup

Unknown POST/enable/step responses are never automatically repeated; preserve the
original journal and require review. Account/permission/source revocation and changed
namespace still stop new effects. Before CLEAN, the registered schedule must have no
next due work, both real roots must be terminal with native/provider usage reconciled,
and no pending/unknown operation may remain. Explicitly disable the owned source when
safe, retain all history and independently verify the same cleanup gate for stored CLEAN
claims. A stopped known business failure is not successful automation execution.

## Other work boundaries

A03/A04 are only prepared for the next increment: reuse public source chunks/revisions/
index-operation observations and controlled embedding reservations, charging index and
Ask search together. Do not expose READY or alter their stock definitions in this A01
increment. A05 input/authorization and F05 approval contract changes need their own review.
Root owns W03/W04 observation/approval/evidence modules; shared hooks are integrated only
after their freeze. Work submissions must use a fixture's actual upload revision with
the existing revision1 default for basic fixtures, not a hardcoded revision1.

## W03/W04 shared integration and W04 consumer proof

The shared initializer uploads the fixed `counter.txt=0` input and submits its actual
returned revision. It delegates exclusively to the separately reviewed approval evidence
module. A normal generic observer never grants an approval. Stored CLEAN and live cleanup
both require that module's settled-resource gate, plus the original operations/session
observations. W04 additionally requires `work-consumer-rebuild-v1` readiness before writes.

Only the controlled target attaches `POST /api/v1/agent-evaluations/profiles/work-counter-consumers/rebuild`
with `{source_run_id}` and its GET receipt. The authenticated attested owner must have
evaluation/run and Work execution permissions, and the original root must use its sealed
W04 lead model. Admission requires WAITING_APPROVAL, no lease/cancellation, one RELEASED
session and one known 10-input/5-output call matching the provider reservation, no operation
receipt, no pending/running Work and a healthy first sandbox reservation. A short bounded
wait permits the same root's genuine transport cleanup to return; no active operation is
cancelled during a rebuild.

The fixed controller durably saves REBUILDING before cooperatively stopping the genuine
WorkWorker serve loop. It verifies deletion of the original worker registration, creates
a new WorkWorker object against the same engine, queue and saver, then verifies its new
registration and unchanged checkpoint ID in thread `work:<root>`. The closed receipt contains
schema_version1/case_id/source_run_id/owner_id/environment_id/namespace/status REBUILT,
before_worker_ids and after_worker_ids (one disjoint UUID each), and equal nonempty
checkpoint_id_before/after. Unknown outcomes keep REBUILDING and prohibit another POST;
only a completed same-scope receipt can be read back. This proves a cooperative consumer
object rebuild when actually exercised; it does not prove an OS process restart.

The controller shares the existing sandbox/provider objects and cannot reset their budgets.
It accepts no arbitrary worker ID, command, path or code. The Java verifier is not connected
by this increment. Author tests use controlled DB/HTTP/queue-availability stand-ins and do
not constitute actual A01/W03/W04 execution, manager release evidence or independent QA.
