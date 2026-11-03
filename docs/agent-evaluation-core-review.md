# Agent evaluation core independent review

2026-11-03. Scope: initial core manifest `services/evaluation/.local/core-freeze/manifest.json`, SHA256 `86123f45395fa4b8f2a1654ef6bcee94af5a45fbc20042c75526be211fece275`. All 19 file hashes matched at review start. The author's21 engineering tests are not independent A20 platform acceptance. The README correctly states specialized profiles are unfinished; this report does not treat future CRUD/UI or A20 integration as implemented.

## R1 — P1 OPEN: missing operation evidence can pass a required zero-effect assertion

`services/evaluation/src/evaluation_runner/facts.py:20` defaults missing operations to an empty list and compares its length. `adapters.py:105` prepopulates operations=[] for every mode, but only Work fetches operation records. An Ask/Workflow case with required operation_count(expected=0) can report PASS without ever observing whether a tool caused an effect. Missing is different from an observed complete empty set.

Independent no-network function probe called verify with only run.status=SUCCEEDED and no operations: fact=PASS and outcome(...,cleanup=CLEAN)=PASS. No platform or model was executed. Require mode-specific complete observation evidence, reject unsupported/missing records as BLOCKED/NEEDS_REVIEW, and distinguish actual operation success/effect evidence from mere record count where appropriate.

## R2 — P1 OPEN: declared budgets can be enlarged or ignored before provider execution

`adapters.py:69` raises Work max_tokens to at least1000 and max_model_calls to at least5, while Budget accepts0. Ask and Workflow submission/observation do not enforce these evaluation budgets or collect actual usage. An explicitly forbidden zero-call/token case may still cause real requests; a small quota cannot authorize a larger one.

Independent submission-only stub accepted Budget(max_tokens=0,max_model_calls=0) and captured the generated Work request with token_limit=1000/step_limit=5. This was no HTTP request and no side effect. Reject quotas that cannot be safely represented, or enforce an equal/stricter budget through a trusted capability; never silently widen. Missing actual usage must remain unknown and cannot provide proof of budget compliance. Ask/Workflow require real enforcement or an explicit pre-submit blocker, not a passing engineering claim.

## R3 — P2 OPEN: metric exception discards already determined factual failure

`engine.py:48` calculates facts, `:50` executes all metrics, and only`:54` persists fact_results. If a metric throws, the generic exception at`:69` records NEEDS_REVIEW while the attempt's fact_results remains[]. A known required factual FAIL is lost and can no longer drive the required FAIL precedence.

Independent engine control-flow probe replaced only initialization/submission/observation/metric boundary functions in memory, used the real missing-artifact verifier and actual local journal/report code, and had the metric raise ValueError. Result: gate=NEEDS_REVIEW, fact_results=[], task_outcome=NEEDS_REVIEW, rather than a preserved required artifact FAIL. No full dataset, API, provider or sandbox ran. Persist facts before scoring; isolate per-metric failures into ERROR records and preserve any required factual FAIL.

## Evidence and status

Probe source: `services/evaluation/.local/core-review/independent_probes.py`; output is preserved in `probe-results.json`. Three bounded probes use function stubs/local temporary journal only, not business20 QA. No production implementation/author test changed by reviewer. Root has asked the author to fix and refreeze; all three items remain OPEN pending independent recheck of the new exact hashes and replay.

Other reviewed paths: target loopback validation and no redirects, authenticated attestation before writes, local journal locking and durable unknown submission, explicit blockers for unsupported profiles/metrics, report partial-batch non-green and factual-over-semantic priority in the pure outcome function. These observations are limited to the read code and targeted boundaries, not a global no-defect assertion. No paid model, browser, production business data or full runner acceptance was performed.


## Independent recheck of48f9 freeze: R1/R2/R3 CLOSED; new R4 OPEN

New manifest `services/evaluation/.local/core-review-fix/manifest.json` SHA256 `48f9b6605dea4e55015df098f3ec84d44cb89eb09557da87461b209837bc2569`: all20 files independently match. profile_support remains excluded. Replayed12 bounded in-memory checks, not A20 acceptance; exact results `services/evaluation/.local/core-review/recheck-48f9-results.json` and source `recheck_48f9.py`.

- R1 CLOSED: missing operation evidence or empty list without complete observation is BLOCKED; complete observed empty list still PASS.
- R2 CLOSED for the original core budget defects: zero Work caps are blocked before any submission; authorized5calls/1000tokens remain unchanged; Ask/Workflow without case-bound proof block; two-root Ask with quota below combined proof blocks; actual UNKNOWN usage blocks, and UNKNOWN first-turn usage prevents second-turn POST. Real enforcement by the attested platform remains a separate integration requirement, not proven by these stubs.
- R3 CLOSED: a real required missing-artifact FAIL remains recorded and gateFAIL when a controlled metric raises; metric becomesERROR without replacing facts. Original probe evidence was adapted only to mark the now-required authoritative artifact/usage observations, not to weaken the failure condition.

### R4 — P1 OPEN: user_message_count still treats missing messages as observed zero

`facts.py` user_message_count branch defaults missing messages to{} and items to[]. A required expected0 on Work/Workflow or otherwise absent message observation returnsPASS and pure outcome gatePASS. Neither the collection nor its completeness was observed. Independent function probe reproduced factPASS/gatePASS with only run.status=COMPLETED. Apply the same explicit complete-observation rule as operations; unsupported mode/missing/malformed/paginated message evidence must block. This is a newly confirmed sibling boundary; it does not reopen the repaired operation branch.

Author33 passed is not counted as independent QA. No full runner/platform test was executed in this recheck. Await R4 repair/refreeze before declaring core review clear.


## R4 final independent recheck: CLOSED

Manifest `services/evaluation/.local/core-r4-freeze/manifest.json`, SHA256 `70702e73e1059677226d90e1df8b7e641a030fd993e5988583f48268333207dc`, independently matches every listed file. Five new pure-function checks replayed missing messages, absent observed flag, Work mode, a paginated Ask collection and a complete empty Ask collection. The first four returnBLOCKED/gateNEEDS_REVIEW; only the complete authoritative Ask observation returnsPASS. Evidence: `services/evaluation/.local/core-review/recheck-r4-results.json`.

R1–R4 are now CLOSED in this initial-core review scope. Author37 tests are not counted as independent A20 acceptance. Specialized fixed profiles, platform budget enforcement, actual business20 execution, true UI and deployment remain separate gates; this review does not certify them.


## Explicit UTF-8 fix independent review and Windows recheck: CLOSED

New content manifest `services/evaluation/.local/core-utf8-freeze/manifest.json`, SHA256 `58551292175caeaec8bb542eebeca0d059715a6be72f38420bec54e2ad4c256f`, independently matches every frozen file before and after execution. Relative to70702, runtime changes are limited to explicitUTF-8 on target/dataset reads in CLI and journal.json reads; the prior regression reads are aligned and a new isolated Windows encoding test is added. Existing A20 JSON content digest and Chinese cases were not ASCII-rewritten; R1–R4 fixes remain present.

Independent execution first ran the new positive/negative encoding pair:2passed in0.66s. It uses the actual Windows interpreter with-I, explicitly verifies locale cp936/936/gbk and utf8_mode=0, a Chinese+emoji filename and UTF-8 content, CLI preflight, journal close/reopen, JSON/Markdown export, and invalid UTF-8 exit2. No-Xutf8 wrapper or ASCII conversion. The Platform in this I/O test is a substituted identity transport, not real target attestation or an A20 run.

After that focused review passed, the new full frozen engineering selection ran39passed/0failed/0skipped in0.99s. The focused2are included in39 and must not be added as41. Evidence `services/evaluation/.local/core-utf8-independent-qa/run_ba2bc0da5205/`: manifest.json, summary.json, utf8_targeted/pytest.log, engineering_39/pytest.log. Python network connections are denied, no provider credentials inherited, no API/database/sandbox or paid evaluation run. Neither batch is A20 business acceptance.

Preflight limitation remains explicit:ATTESTED+case_count currently proves only environment identity/schema parsing, not per-case permission/profile/dependency/budget readiness. The author has been told to add read-only case readiness with explicitBLOCKED dependencies during fixed-profile integration; no new confirmedP1is asserted merely for this declared incomplete feature, and ATTESTED must not be presented as20 executable/passing tasks.


## AE-05 strict parameter-contract repair independent review: CLOSED

Reviewed manifest `services/evaluation/.local/core-strict-freeze/manifest.json`, SHA256 `7881a2fde54cb4d1aadabd6ff2f78f183b0e29d6b099423d11be16abb94fdab7`; every recorded byte hash matched before and after review/QA. The root's original controlled observation accepted Check(version=True,operation_count.expected=False) and returnedPASS; on the repaired source those values are rejected, independently and together.

Independent28 bounded checks cover raw count booleans/floats/strings/negatives, version booleans with otherwise valid model input,9 noncanonical artifact paths, bad digest, unknown metric, implicit available_tools judge option, nonfinite thresholds/nested JSON, exactFalse versus0 (including nested values), and an unimplemented registered protected_tests check. A valid canonical path/digest and identicalFalse still work. Forward registration does not create capability: actual core engine with MockTransport returnsNEEDS_REVIEW/startedFalse and makes no businessPOST for the unimplemented check. Evidence `services/evaluation/.local/core-strict-independent-qa/review-probes.json`, scriptreview.py. No platform/provider was used;28checks are review probes, not28Agent tasks.

After review closure, root-authorized independent execution of all6 frozen engineering test files completed **54passed,0failed,0skipped in1.06s**, using the separate evaluation venv, actual local DeepEval metric and controlled HTTP substitutions. Python socket access is denied and provider credentials are not inherited. Frozen source/test hashes unchanged. Evidence `services/evaluation/.local/core-strict-independent-qa/run_f20d23585db8/{manifest.json,summary.json,pytest.log}`.

54is the current engineering selection, not added to prior39, not82with the probes, and not A20 actual business acceptance. All original R1–R4/UTF-8 fixes remain present. No new confirmed finding in this strict-contract review scope; fixed-profile bodies, target budget enforcement, real A20, UI and deployment retain independent gates.
