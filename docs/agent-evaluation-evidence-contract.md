# A02 retained facts and command audit

This contract applies to `evaluation_runner.fixed_run`, currently only the implemented
AET-A02 controlled engineering case. It does not add an A20 acceptance claim. The first
real A02 run retains its original code gate PASS and acceptance NEEDS_REVIEW_R02;
its dropped database is not reconstructed or used to manufacture missing evidence.

Before grading and cleanup, the runner atomically writes one UTF-8 JSON fact file under
`evidence/a02-facts/<uuid>.json`, then durably records its reference in
`attempt.evidence_refs`. The reference contains schema_version=1, kind, canonical
relative path and the file-byte SHA256. References reject absolute paths, traversal,
links/junctions, missing or oversized files and changed hashes. Every prior reference
is checked; selecting a newer observation does not discard older evidence.

The closed A02 projection binds owner, environment, attempt, suite, repeat, candidate,
catalog, case definition and rules. It retains two official run IDs and terminal states;
complete message roles/run IDs, input text digests, output JSON digests and the declared
fictional project/people fields; bounded service context booleans and human-context
digests; and six native usage counters. It contains no arbitrary tool arguments,
tool responses, headers, keys, hidden reasoning or unexpected output strings. Official
run IDs must match the requested roots. Observed messages must belong to these roots.
The final JSON, two declared user inputs, final model context and prior terminal state
are independently recomputed from retained files. Missing required evidence blocks
grading/cleanup; saved PASS assertions cannot substitute for those facts.

Each fixed CLI command with an output directory appends a session to
`network-audit-index.json`: PENDING before its action, then SEALED with an immutable
`evidence/network-audit/<uuid>.json` hash reference. Failed commands keep ERROR receipts;
an interrupted/unsealed session prevents subsequent successful evidence validation.
Run, cleanup and offline report append sessions and preserve all earlier references.
`describe` without an output directory returns an inline audit and its digest on stdout;
with an output directory it also persists its audit. No target is required for offline
report or describe; their policy permits no socket destinations.

The audit binds action/session, case/catalog/rules/candidate and, when available,
owner/environment/target digest and suite/repeat. It records guard enabled, start/end,
policy digest, phase start/end/status/event counts (including zero-event phases), and
only event type, phase and allowed boolean for socket observations. It never records
hostnames, URL paths/query, request headers or authentication. The permitted online
origin is the validated loopback target; redirects are disabled. The `import` phase
covers the lazy DeepEval import; `metric` covers actual deterministic metric measurement;
`execution` covers formal API activity; `export` covers staging report preparation.
A successful A02 requires a complete run session with all four phases and no denied
socket event. This is Python evaluator audit-hook evidence, not an OS firewall claim,
platform-worker/native-process audit, full SSE capture or complete agent trajectory.

Exports are first rendered to a private staging directory. After that export phase
completes, the audit is sealed and all sockets, including loopback, are rejected during
the remaining local publication. The final local serialization attaches the sealed
references. JSON, JUnit and Markdown are replaced separately; the runner does not claim
a three-file atomic transaction. The last atomic `report-ready.json` lists the three
file hashes, identity and audit references. A missing/stale/mismatching receipt is not
a valid new PASS. Readers must call `fixed_audit.validate_ready(directory)`, which also
rechecks retained facts and mandatory run/metric audit, before using PASS or deleting
the isolated target. A bare report.json/JUnit PASS is insufficient. Failed export or
audit persistence produces a nonzero command result and does not publish a new valid
PASS receipt. Already established factual FAIL remains FAIL in the journal.

These are local integrity and bounded retention checks, not signed attestations against
a malicious operator who can rewrite the entire result directory. Generic baseline CLI
reports do not implement this fixed-case contract and must not be substituted for it.
New real A02 acceptance requires an independently reviewed fresh run; author unit and
loopback socket tests do not constitute that execution or UI/production release.
