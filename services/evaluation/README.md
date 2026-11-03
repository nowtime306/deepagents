# Independent Agent evaluation runner

This package uses its own Python 3.12 environment and `uv.lock`. It does not import
the platform's Python application or use its environment files. DeepEval 4.3.2 is
locked independently of the API environment. Do not run it inside the API venv.

The CLI is `python -m evaluation_runner {preflight,run,report,cleanup}`.
`preflight` and `run` require `--target-manifest`, `--dataset`, and `--output`.
`report` requires only `--output`; `cleanup` requires a target manifest and output.
Authentication uses `EVALUATION_USERNAME` and `EVALUATION_PASSWORD`, never CLI
arguments or dataset fields. The target must attest the registered environment,
owner, commit, migrations and `controlled-v1` fixture profile. Loopback alone does
not establish isolation. Attestation is an operator registration, not proof that
an arbitrary host is safe.

Target JSON fields: `schema_version: 1`, `environment_id` (UUID), `api_base_url`
(explicit loopback HTTP port), `platform_commit` (40 hex), `owner_id` (UUID),
`required_migrations` (filenames), `fixture_profile: {id, version}`, and
`resource_bindings` (reviewed case/fixture names to IDs in the isolated target).
The model binding for a case uses its stable case ID. A published-workflow fixture
also specifies its workflow binding and explicit business version.

Outputs are `journal.json`, `report.json`, `results.xml`, and `report.md`.
`preflight` writes `preflight.json`. Reports distinguish factual failure,
unscored/missing evidence, and cleanup uncertainty. A partial batch cannot have a
passing gate. Exit codes: 0 gate passed; 1 failure/review required; 2 invalid
contract; 3 infrastructure failure; 130 cancellation. Write `cancel.request` in
the output directory to stop new work and request cancellation of a known active
run. No OS process termination is required. Unknown submissions are never
automatically posted again; reconcile the original target rather than delete
the journal. OS-backed journal locking prevents two local consumers and is
released by the OS after a process exits.

The current core supports formal Ask conversation runs, Coding Work task runs,
and explicit published Workflow runs. It creates fresh task/conversation IDs,
registers them for exact cleanup, and preserves uncertain writes/receipts.
Workflow fixtures are provisioned business inputs and are not deleted by the
core. `datasets/a20.json` is the 5/8/7 task specification, **not 20 passing
executions**. Specialized profiles and trusted observers are still being
implemented with the platform/operations roles; they return explicit blockers
until their complete init/observe/cleanup chains exist. This is ongoing work by
the implementers, not an integration requirement delegated to the user.

DeepEval ToolCorrectness is called with an explicit controlled judge and `llm`
mode, without `available_tools`. Judge generation raises if unexpectedly used.
Parameter/output/exact comparison is enabled explicitly. The result is
deterministic engineering evidence, not semantic model quality. Missing complete
tool evidence remains NOT_SCORED. Full-trajectory TaskCompletion is not enabled.
No Confident AI login/upload or external supplier fallback is implemented.
Privacy controls are set before import; the CLI observes Python socket events
and blocks destinations outside the registered loopback port. This is not an
operating-system firewall or proof about unobserved native/subprocess networking.

Author verification of the initial core: 21 synchronous tests passed. After
independent review, R1/R2/R3 regression coverage brings the current core to 33
passing tests. R4 adds four cases (37 passing total) requiring complete, explicitly
observed Ask messages before counting them; missing/inapplicable messages cannot
pass an expected zero. Required operation-count facts need explicit complete observations;
missing evidence is not zero. Work budgets below the endpoint minimum are rejected
before fixture creation, and accepted budgets are sent unchanged. Ask/Workflow
require a matching server `execution_limits` proof for the registered case,
including all roots in a multi-turn case; absent or insufficient proof blocks
submission. Missing/unknown actual usage stops subsequent dispatch. Factual results
are persisted before scoring, and scorer errors cannot replace factual failure.
The initial 21-test freeze remains a separate historical record. These tests
include HTTP transport substitutes for formal API paths and a real installed
DeepEval metric; they are not real platform/PG/Coding A20 acceptance. A separate
guarded metric import/measure/export probe passed with zero judge generations
and zero observed Python socket events. Independent review and full A20
integration remain required. UI and paid semantic evaluation are untested.
