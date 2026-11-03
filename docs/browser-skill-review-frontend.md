# BrowserSkill frontend independent static review

Reviewed in `worktrees/browser-skill` on 2026-11-03. This review reads the five feature
UI files (seven after the UX display increment), their immediate API contracts, owner-scoped skill lookup, recording publish
behavior and Work replay validation. No business source was changed by the reviewer.
No browser, production page, 3131 test page, login or model was used. The existing
webpack build log was inspected; visual behavior is not independently certified here.

## Findings and incremental review

**Resolved [P2]: leaving the browser view bypassed the unsaved-draft confirmation.**

`apps/web/src/features/capabilities/capability-center.tsx:240` directly calls
`setSkillView("library")`. The conditional at the BrowserSkills mount then unmounts
the component, discarding RecordingDetail's local title/description/parameter state.
The initial confirmation in `browser-skills.tsx` protected only its own “返回录制列表”
button; the parent did not receive or consult `detailDirty`. Top-level capability tabs
also unmounted the browser view without checking that state.

Reproduction from code: open an unpublished review record, edit a parameter name or
description without saving, click “技能库与习惯学习”, then return to “浏览器录制” and
reopen the record. The unsaved edit has been discarded without the existing discard
prompt. Root's incremental fix is now independently confirmed: BrowserSkills reports
detail/create input dirtiness to the parent, both parent navigation paths consult
`leaveBrowser`, selecting the current capability tab returns without resetting state,
and new-record navigation confirms before discarding a dirty detail and clears old
creation fields. Declining confirmation returns before unmount/reset. The live flag
and per-request sequence guards remain after the cleanup simplification. There are no
remaining actionable findings from this static review; this is not interactive QA.

## Verified properties

- Approval fetches `/capabilities/skills/{id}`; the API selects by current owner. It
  reads real `trajectory.json` and compares enabled state, name, version, step count,
  all parameter names/values and supported nonblocked steps before marking the
  exact request checked. Work additionally validates the immutable published version.
- The rendered approval component key and gate key include approval ID, digest,
  action index and serialized arguments. Changed arguments remount the validator;
  abort/unmount clears the old check. The approve button waits for all browser-skill
  checks. Reject does not depend on a successful path fetch or check.
- The entire supported path and substituted fill/select parameters are rendered
  with the explicit local authenticated browser and network-boundary explanation.
  The new `work-output` class contains long parameter text using existing styling.
- The local-browser checkbox is hidden from non-admin accounts, defaults false,
  resets when changing environment, and resets after a successful new run. Request
  construction also requires admin plus browser environment. Task panels remount by
  task ID in the task workbench. Backend enforcement remains mandatory and present.
- Browser creation/control buttons require the intended admin/manage flags. Ordinary
  users are not shown connection details or the local opt-in. Owner-scoped record
  reads remain available by API design; the recordings view itself is not admin-only.
- Record mutations distinguish a completed server operation from subsequent refresh
  failure and explicitly tell users not to repeat a committed operation. Publication
  itself is idempotent at the API. The internal detail-to-list action guards dirty edits.
- Root's last publication fix is confirmed: `skill_id` derives the published label
  for list/detail, and published records no longer render the editable review form
  or publish action even though the API retains `status=review`.
- UX increment: knowledge results no longer render `results.guidance`; backend data,
  sources, per-item scores/warnings and empty-state rendering remain. The replacement
  candidate-count guidance does not claim answer confidence.
- `SkillResourcePreview` strips a leading optional-BOM YAML frontmatter only for the
  exact `SKILL.md` path, shows it as collapsed escaped plain text, and passes the
  remaining original body to the existing Markdown renderer. Other `.md` paths retain
  their prior renderer and other text retains escaped `pre`; no package data is mutated.
- Recording rows derive the safe URL/step count from the server-normalized draft and
  show `updated_at`. The published-skill preview fetches the actual owner-scoped skill
  detail and uses the shared preview dialog. These additions introduce no new mutation
  or permission path.

## Validation boundaries and operational QA runner

The root author reports strict TypeScript, targeted lint and production webpack build
success; both `.local/browser-skill-frontend-build-webpack.log` and the final increment's
`.local/browser-skill-frontend-build-final.log` end with successful static builds.
This reviewer did not repeat a full application build or perform page interaction.
The finding above is established by the navigation/render/state control flow.

Independent operational admission checks can be run without an API, database or model:

```powershell
./scripts/test-browser-skill-operations.ps1 `
  -Executable <verified-official-bsk.exe> `
  -Extension <verified-official-extension-directory> `
  -Chromium <installed-playwright-chromium.exe> `
  -Playwright <project-node_modules/@playwright/test/index.js>
```

The runner creates a unique workspace-local empty profile, refuses occupied dedicated
ports, checks real start/check/repeated start/normal stop, injects an invalid PID only
into its own fixture state to prove refusal, and checks foreign-listener refusal. It
retains `qa-transcript.log`, `qa-result.json` and the owned state under the printed
`.local/browser-skill-ops-qa-*` path. It performs no recursive cleanup and opens no pages.
The frozen runtime scripts are not changed by this review or runner.

The database deployment gate checks remain in
`services/api/tests/integration_tests/test_deployment_gate.py`; they require the
existing dedicated `_test` database setup, never the production database.

Final operational runner evidence: `.local/browser-skill-ops-qa-e3e15ac979b64890bbc2894029f55ec4/qa-result.json`
is passing, including the forced pre-CDP failure cleanup. Its transcript and both normal
and failed-start process manifests are retained. The previous wrong-worker and cleanup
failures are preserved separately; the final runner confirms those fixes.
