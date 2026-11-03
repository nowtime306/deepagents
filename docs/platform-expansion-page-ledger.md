# 默认页面库存逐项验收记录

以canonical文件名与完整用例标题唯一标识；行号仅便于定位。分批重复运行不增加库存。103条 / 32文件，最新101通过、2按用户要求隐藏跳过。mock-only不代表真实端到端；real-api-with-local-or-page-stubs保守标明该用例含本机协议或局部页面替身。真实外部模型/供应商效果未验。

## PAGE-001 · accounts.spec.ts:45
- 标题：actual registration approval, viewer permissions and password revocation
- 最新状态：passed；范围：real-api-with-local-or-page-stubs
- 证据：`.local/team-workspace/platform-expansion/qa/owner_agent_e2e_375d5ca77013_test/stage-one-browser.log`

## PAGE-002 · accounts.spec.ts:166
- 标题：actual administrator create and reset require the user to change temporary password
- 最新状态：passed；范围：real-api-with-local-or-page-stubs
- 证据：`.local/team-workspace/platform-expansion/qa/owner_agent_e2e_375d5ca77013_test/stage-one-browser.log`

## PAGE-003 · accounts.spec.ts:259
- 标题：actual custom role and invitation grant only selected capabilities
- 最新状态：passed；范围：real-api-with-local-or-page-stubs
- 证据：`.local/team-workspace/platform-expansion/qa/owner_agent_e2e_375d5ca77013_test/stage-one-browser.log`

## PAGE-004 · automations.spec.ts:7
- 标题：schedule edit, real worker, signed notice, persistent batch and mobile
- 最新状态：passed；范围：real-api-with-local-or-page-stubs
- 证据：`.local/team-workspace/platform-expansion/qa/owner_agent_e2e_375d5ca77013_test/stage-one-complete-profile.log`

## PAGE-005 · browser-workbench.spec.ts:7
- 标题：native browser viewing, exclusive manual input, safe close and mobile layout
- 最新状态：passed；范围：real-native-zero-model
- 证据：`.local/team-workspace/platform-expansion/qa/owner_agent_e2e_375d5ca77013_test/stage-three-browser.log`

## PAGE-006 · chat-fluidity.spec.ts:29
- 标题：switching conversations clears stale drafts, blocks loading sends and rejects late history
- 最新状态：passed；范围：mock-only
- 证据：`.local/team-workspace/platform-expansion/qa/mock-browser-full.log`

## PAGE-007 · chat-fluidity.spec.ts:62
- 标题：composer grows and shrinks, Shift Enter inserts a line and IME confirmation never sends
- 最新状态：passed；范围：mock-only
- 证据：`.local/team-workspace/platform-expansion/qa/mock-browser-full.log`

## PAGE-008 · chat-fluidity.spec.ts:86
- 标题：reading older messages pauses streaming scroll and latest button resumes following
- 最新状态：passed；范围：mock-only
- 证据：`.local/team-workspace/platform-expansion/qa/mock-browser-full.log`

## PAGE-009 · chat-fluidity.spec.ts:143
- 标题：prepending older history preserves the visible reading anchor
- 最新状态：passed；范围：mock-only
- 证据：`.local/team-workspace/platform-expansion/qa/mock-browser-full.log`

## PAGE-010 · chat-fluidity.spec.ts:159
- 标题：expanding a long tool process keeps the clicked summary in view
- 最新状态：passed；范围：mock-only
- 证据：`.local/team-workspace/platform-expansion/qa/mock-browser-full.log`

## PAGE-011 · chat-message-actions.spec.ts:4
- 标题：only final answers show answer actions across tool steps, older turns and reload
- 最新状态：passed；范围：mock-only
- 证据：`.local/team-workspace/platform-expansion/qa/mock-browser-full.log`

## PAGE-012 · chat-message-actions.spec.ts:63
- 标题：live tool steps update the process and late progress reads cannot replace the final answer
- 最新状态：passed；范围：mock-only
- 证据：`.local/team-workspace/platform-expansion/qa/mock-browser-full.log`

## PAGE-013 · conversation-references.spec.ts:14
- 标题：private conversation references persist, refresh explicitly, estimate tokens and fit mobile
- 最新状态：passed；范围：real-api
- 证据：`.local/team-workspace/platform-expansion/qa/owner_agent_e2e_375d5ca77013_test/stage-three-references.log`

## PAGE-014 · expansion-core-qa.spec.ts:42
- 标题：PE-TC-011/013/024 readonly monitoring keeps unknown and removes mutation actions
- 最新状态：passed；范围：mock-only
- 证据：`.local/team-workspace/platform-expansion/qa/mock-browser-full.log`

## PAGE-015 · expansion-core-qa.spec.ts:58
- 标题：PE-TC-020 rule failed save preserves editable input
- 最新状态：passed；范围：mock-only
- 证据：`.local/team-workspace/platform-expansion/qa/mock-browser-full.log`

## PAGE-016 · expansion-core-qa.spec.ts:69
- 标题：PE-TC-110 four compatibility labels do not claim real service connectivity
- 最新状态：passed；范围：mock-only
- 证据：`.local/team-workspace/platform-expansion/qa/mock-browser-full.log`

## PAGE-017 · expansion-core-qa.spec.ts:78
- 标题：PE-TC-140/141 synthetic perfect recall still fails unreviewed citation gate
- 最新状态：passed；范围：mock-only
- 证据：`.local/team-workspace/platform-expansion/qa/mock-browser-full.log`

## PAGE-018 · expansion-identity-tools.spec.ts:64
- 标题：PE-TC-041 local MFA202 never enters app and expired challenge can restart
- 最新状态：passed；范围：mock-only
- 证据：`.local/team-workspace/platform-expansion/qa/mock-browser-full.log`

## PAGE-019 · expansion-identity-tools.spec.ts:79
- 标题：PE-TC-035 enterprise pending challenge returns to enterprise and local login
- 最新状态：passed；范围：mock-only
- 证据：`.local/team-workspace/platform-expansion/qa/mock-browser-full.log`

## PAGE-020 · expansion-identity-tools.spec.ts:87
- 标题：PE-TC-051/053 recovery UI keeps MFA proof and uses generic request response
- 最新状态：passed；范围：mock-only
- 证据：`.local/team-workspace/platform-expansion/qa/mock-browser-full.log`

## PAGE-021 · expansion-identity-tools.spec.ts:103
- 标题：PE-TC-030 identity provider save failure preserves fields and issuer stays immutable
- 最新状态：passed；范围：mock-only
- 证据：`.local/team-workspace/platform-expansion/qa/mock-browser-full.log`

## PAGE-022 · expansion-identity-tools.spec.ts:119
- 标题：PE-TC-040/042 personal security displays recovery codes once
- 最新状态：passed；范围：mock-only
- 证据：`.local/team-workspace/platform-expansion/qa/mock-browser-full.log`

## PAGE-023 · expansion-identity-tools.spec.ts:134
- 标题：PE-TC-060 transport sse form sends correct protocol and scope
- 最新状态：passed；范围：mock-only
- 证据：`.local/team-workspace/platform-expansion/qa/mock-browser-full.log`

## PAGE-024 · expansion-identity-tools.spec.ts:134
- 标题：PE-TC-060 transport stdio form sends correct protocol and scope
- 最新状态：passed；范围：mock-only
- 证据：`.local/team-workspace/platform-expansion/qa/mock-browser-full.log`

## PAGE-025 · expansion-identity-tools.spec.ts:134
- 标题：PE-TC-060 transport acp form sends correct protocol and scope
- 最新状态：passed；范围：mock-only
- 证据：`.local/team-workspace/platform-expansion/qa/mock-browser-full.log`

## PAGE-026 · expansion-identity-tools.spec.ts:154
- 标题：PE-TC-070/072 OAuth component loads per-account state and revokes
- 最新状态：passed；范围：mock-only
- 证据：`.local/team-workspace/platform-expansion/qa/mock-browser-full.log`

## PAGE-027 · expansion-identity-tools.spec.ts:165
- 标题：PE-TC-150 mobile login identity controls stay within viewport
- 最新状态：passed；范围：mock-only
- 证据：`.local/team-workspace/platform-expansion/qa/mock-browser-full.log`

## PAGE-028 · expansion-identity-tools.spec.ts:174
- 标题：PE-TC-030 provider name and enabled state combine without mutating sources
- 最新状态：passed；范围：mock-only
- 证据：`.local/team-workspace/platform-expansion/qa/final-ui-patches.log`

## PAGE-029 · expansion-identity-tools.spec.ts:188
- 标题：PE-TC-150 provider empty search can clear on mobile and retains list actions
- 最新状态：passed；范围：mock-only
- 证据：`.local/team-workspace/platform-expansion/qa/final-ui-patches.log`

## PAGE-030 · expansion-media-qa.spec.ts:40
- 标题：PE-TC-090/102 image upload uses pixels and UNKNOWN cancel waits for cleanup
- 最新状态：passed；范围：mock-only
- 证据：`.local/team-workspace/platform-expansion/qa/mock-browser-full.log`

## PAGE-031 · expansion-media-qa.spec.ts:60
- 标题：PE-TC-120 media model dropdown and both price/bill pages remain reachable
- 最新状态：passed；范围：mock-only
- 证据：`.local/team-workspace/platform-expansion/qa/mock-browser-full.log`

## PAGE-032 · expansion-media-qa.spec.ts:79
- 标题：PE-TC-123 CSV explicit mapping previews before import and rejects out of scope
- 最新状态：passed；范围：mock-only
- 证据：`.local/team-workspace/platform-expansion/qa/mock-browser-full.log`

## PAGE-033 · expansion-ui-fixes.spec.ts:23
- 标题：provider list supports search, status and empty state without losing editor
- 最新状态：passed；范围：mock-only
- 证据：`.local/team-workspace/platform-expansion/qa/owner_agent_e2e_375d5ca77013_test/stage-three-configuration.log`

## PAGE-034 · expansion-ui-fixes.spec.ts:40
- 标题：valid large decimal cost fits the mobile monitoring cards
- 最新状态：passed；范围：mock-only
- 证据：`.local/team-workspace/platform-expansion/qa/owner_agent_e2e_375d5ca77013_test/stage-three-configuration.log`

## PAGE-035 · expansion-window-qa.spec.ts:3
- 标题：BUG005 selected window keeps precise currencies and ignores late old result
- 最新状态：passed；范围：mock-only
- 证据：`.local/team-workspace/platform-expansion/qa/final-ui-patches.log`

## PAGE-036 · interface-refresh.spec.ts:15
- 标题：grouped workspace navigation, named usage and compact audit preserve actual records
- 最新状态：passed；范围：real-api-with-local-or-page-stubs
- 证据：`.local/team-workspace/platform-expansion/qa/owner_agent_e2e_375d5ca77013_test/stage-two-independent.log`

## PAGE-037 · interface-refresh.spec.ts:52
- 标题：mobile navigation closes after selection and long unknown records remain readable
- 最新状态：passed；范围：real-api-with-local-or-page-stubs
- 证据：`.local/team-workspace/platform-expansion/qa/owner_agent_e2e_375d5ca77013_test/stage-two-independent.log`

## PAGE-038 · interface-refresh.spec.ts:75
- 标题：a slow model refresh after save does not erase newly entered filters
- 最新状态：passed；范围：real-api-with-local-or-page-stubs
- 证据：`.local/team-workspace/platform-expansion/qa/owner_agent_e2e_375d5ca77013_test/stage-two-independent.log`

## PAGE-039 · knowledge-center-ui.spec.ts:71
- 标题：KB-01/02/09/11/13 list navigation loads advanced data on demand and hides standalone media entries
- 最新状态：passed；范围：mock-only
- 证据：`.local/team-workspace/platform-expansion/qa/mock-browser-full.log`

## PAGE-040 · knowledge-center-ui.spec.ts:110
- 标题：KB-03/05/06 file filtering, preview and deletion preserve context and recover after failure
- 最新状态：passed；范围：mock-only
- 证据：`.local/team-workspace/platform-expansion/qa/mock-browser-full.log`

## PAGE-041 · knowledge-center-ui.spec.ts:135
- 标题：KB-04/12 upload validates before requesting, blocks duplicates and refreshes after success
- 最新状态：passed；范围：mock-only
- 证据：`.local/team-workspace/platform-expansion/qa/mock-browser-full.log`

## PAGE-042 · knowledge-center-ui.spec.ts:166
- 标题：KB-07 retrieval has explicit sources, clears stale results on error and explains empty evidence
- 最新状态：passed；范围：mock-only
- 证据：`.local/team-workspace/platform-expansion/qa/mock-browser-full.log`

## PAGE-043 · knowledge-center-ui.spec.ts:185
- 标题：KB-10 read-only knowledge users can browse without management controls
- 最新状态：passed；范围：mock-only
- 证据：`.local/team-workspace/platform-expansion/qa/mock-browser-full.log`

## PAGE-044 · knowledge-center-ui.spec.ts:198
- 标题：KB-01/11 list errors offer retry instead of a false empty library
- 最新状态：passed；范围：mock-only
- 证据：`.local/team-workspace/platform-expansion/qa/mock-browser-full.log`

## PAGE-045 · knowledge-center-ui.spec.ts:207
- 标题：KB-01/11 an initial pending request is not shown as a successfully empty library
- 最新状态：passed；范围：mock-only
- 证据：`.local/team-workspace/platform-expansion/qa/mock-browser-full.log`

## PAGE-046 · knowledge-center-ui.spec.ts:221
- 标题：KB-02 late document reads from the previous library never replace the current library
- 最新状态：passed；范围：mock-only
- 证据：`.local/team-workspace/platform-expansion/qa/mock-browser-full.log`

## PAGE-047 · knowledge-operations.spec.ts:6
- 标题：document revisions, reviewed OCR, durable experiment comparison and answer feedback
- 最新状态：passed；范围：real-api-with-local-or-page-stubs
- 证据：`.local/team-workspace/platform-expansion/qa/owner_agent_e2e_375d5ca77013_test/stage-one-complete-profile.log`

## PAGE-048 · management-lists.spec.ts:5
- 标题：management resources show lists first and organization dialogs preserve failed edits
- 最新状态：passed；范围：real-api-with-local-or-page-stubs
- 证据：`.local/team-workspace/platform-expansion/qa/owner_agent_e2e_375d5ca77013_test/stage-one-browser.log`

## PAGE-049 · management-pagination.spec.ts:4
- 标题：management lists reach older items and search beyond the first page
- 最新状态：passed；范围：mock-only
- 证据：`.local/team-workspace/platform-expansion/qa/mock-browser-full.log`

## PAGE-050 · management-pagination.spec.ts:37
- 标题：older workflow drafts save and older runs are polled by exact ID
- 最新状态：passed；范围：mock-only
- 证据：`.local/team-workspace/platform-expansion/qa/mock-browser-full.log`

## PAGE-051 · memories.spec.ts:6
- 标题：memory review, scoped semantic recall, metering and source deletion
- 最新状态：passed；范围：real-api-with-local-or-page-stubs
- 证据：`.local/team-workspace/platform-expansion/qa/owner_agent_e2e_375d5ca77013_test/stage-one-complete-profile.log`

## PAGE-052 · monitoring.spec.ts:14
- 标题：monitoring shows real-source states, editable rules, acknowledgement distinct from recovery and mobile
- 最新状态：passed；范围：mock-only
- 证据：`.local/team-workspace/platform-expansion/qa/mock-browser-full.log`

## PAGE-053 · monitoring.spec.ts:70
- 标题：missing or stale monitoring never claims healthy and failed reads offer retry
- 最新状态：passed；范围：mock-only
- 证据：`.local/team-workspace/platform-expansion/qa/mock-browser-full.log`

## PAGE-054 · platform.spec.ts:7
- 标题：actual ten model purposes, provider presets, filtering and editing
- 最新状态：passed；范围：real-api-with-local-or-page-stubs
- 证据：`.local/team-workspace/platform-expansion/qa/owner_agent_e2e_375d5ca77013_test/stage-two-independent.log`

## PAGE-055 · platform.spec.ts:165
- 标题：SSE preserves split frames and ignores heartbeat
- 最新状态：passed；范围：unit-only
- 证据：`.local/team-workspace/platform-expansion/qa/owner_agent_e2e_375d5ca77013_test/stage-two-remaining.log`

## PAGE-056 · platform.spec.ts:175
- 标题：Chinese login, conversation, streamed reply and history
- 最新状态：passed；范围：mock-only
- 证据：`.local/team-workspace/platform-expansion/qa/owner_agent_e2e_375d5ca77013_test/stage-two-fixtures-fixed.log`

## PAGE-057 · platform.spec.ts:326
- 标题：actual local login and database model management
- 最新状态：passed；范围：real-api-with-local-or-page-stubs
- 证据：`.local/team-workspace/platform-expansion/qa/owner_agent_e2e_375d5ca77013_test/stage-two-independent.log`

## PAGE-058 · platform.spec.ts:540
- 标题：actual capability center uploads, retrieval, skill resources and MCP settings
- 最新状态：passed；范围：real-api-with-local-or-page-stubs
- 证据：`.local/team-workspace/platform-expansion/qa/owner_agent_e2e_375d5ca77013_test/stage-two-independent.log`

## PAGE-059 · platform.spec.ts:722
- 标题：speech page submits the selected model and shows generated audio
- 最新状态：hidden-skip；范围：user-hidden-independent-page
- 证据：`.local/team-workspace/platform-expansion/qa/owner_agent_e2e_375d5ca77013_test/stage-two-remaining.log`

## PAGE-060 · platform.spec.ts:809
- 标题：vision page submits a picture and shows the answer
- 最新状态：hidden-skip；范围：user-hidden-independent-page
- 证据：`.local/team-workspace/platform-expansion/qa/owner_agent_e2e_375d5ca77013_test/stage-two-remaining.log`

## PAGE-061 · platform.spec.ts:883
- 标题：native search citations appear as clickable links in conversation history
- 最新状态：passed；范围：mock-only
- 证据：`.local/team-workspace/platform-expansion/qa/owner_agent_e2e_375d5ca77013_test/stage-two-remaining.log`

## PAGE-062 · platform.spec.ts:931
- 标题：work task drafts persist and can be edited and deleted
- 最新状态：passed；范围：real-api-with-local-or-page-stubs
- 证据：`.local/team-workspace/platform-expansion/qa/owner_agent_e2e_375d5ca77013_test/stage-two-independent.log`

## PAGE-063 · platform.spec.ts:994
- 标题：work run Chinese progress, approval and download controls
- 最新状态：passed；范围：hybrid-real-draft-mock-execution
- 证据：`.local/team-workspace/platform-expansion/qa/owner_agent_e2e_375d5ca77013_test/stage-two-remaining.log`

## PAGE-064 · platform.spec.ts:1173
- 标题：actual document attachment persists across archive and restore
- 最新状态：passed；范围：real-api-with-local-or-page-stubs
- 证据：`.local/team-workspace/platform-expansion/qa/owner_agent_e2e_375d5ca77013_test/stage-two-independent.log`

## PAGE-065 · platform.spec.ts:1263
- 标题：actual workflow canvas saves, executes transforms and publishes a tested version
- 最新状态：passed；范围：real-api-with-local-or-page-stubs
- 证据：`.local/team-workspace/platform-expansion/qa/owner_agent_e2e_375d5ca77013_test/stage-two-independent.log`

## PAGE-066 · platform.spec.ts:1309
- 标题：actual private memory management and version scoped API publication
- 最新状态：passed；范围：real-api-with-local-or-page-stubs
- 证据：`.local/team-workspace/platform-expansion/qa/owner_agent_e2e_375d5ca77013_test/stage-two-independent.log`

## PAGE-067 · platform.spec.ts:1561
- 标题：actual task input files, empty organization and operations pages
- 最新状态：passed；范围：real-api-with-local-or-page-stubs
- 证据：`.local/team-workspace/platform-expansion/qa/owner_agent_e2e_375d5ca77013_test/stage-two-independent.log`

## PAGE-068 · platform.spec.ts:1695
- 标题：actual Agent release pins a version in a document conversation
- 最新状态：passed；范围：real-api-with-local-or-page-stubs
- 证据：`.local/team-workspace/platform-expansion/qa/owner_agent_e2e_375d5ca77013_test/stage-two-remaining.log`

## PAGE-069 · platform.spec.ts:1821
- 标题：actual chat image upload, native search preference and refreshed message preview
- 最新状态：passed；范围：hybrid-real-upload-mock-model-events
- 证据：`.local/team-workspace/platform-expansion/qa/owner_agent_e2e_375d5ca77013_test/stage-two-fixtures-fixed.log`

## PAGE-070 · platform.spec.ts:2064
- 标题：actual local Harness loop, draft estimate and persistent per-turn tokens
- 最新状态：passed；范围：real-api-with-local-or-page-stubs
- 证据：`.local/team-workspace/platform-expansion/qa/owner_agent_e2e_375d5ca77013_test/stage-two-remaining.log`

## PAGE-071 · repositories.spec.ts:17
- 标题：owned repository settings preserve/clear credentials and narrow layout
- 最新状态：passed；范围：real-api-with-local-or-page-stubs
- 证据：`.local/team-workspace/platform-expansion/qa/owner_agent_e2e_375d5ca77013_test/stage-two-independent.log`

## PAGE-072 · repositories.spec.ts:58
- 标题：repository review approval, unknown check and draft PR use an explicit local protocol stand-in
- 最新状态：passed；范围：mock-only
- 证据：`.local/team-workspace/platform-expansion/qa/owner_agent_e2e_375d5ca77013_test/stage-two-remaining.log`

## PAGE-073 · repository-native.spec.ts:9
- 标题：actual native Git task approves one fixed script, persists deletion and fits mobile
- 最新状态：passed；范围：real-native-zero-model
- 证据：`.local/team-workspace/platform-expansion/qa/owner_agent_e2e_375d5ca77013_test/stage-three-git.log`

## PAGE-074 · resource-pagination.spec.ts:3
- 标题：older repositories load once, failed pages retry and search reaches the server
- 最新状态：passed；范围：mock-only
- 证据：`.local/team-workspace/platform-expansion/qa/mock-browser-full.log`

## PAGE-075 · resource-pagination.spec.ts:34
- 标题：batch and occurrence load-more preserve the selected schedule and prior rows
- 最新状态：passed；范围：mock-only
- 证据：`.local/team-workspace/platform-expansion/qa/mock-browser-full.log`

## PAGE-076 · routing-billing.spec.ts:5
- 标题：actual route receiver consent, immutable price and personal budget edit fit narrow layout
- 最新状态：passed；范围：real-api
- 证据：`.local/team-workspace/platform-expansion/qa/owner_agent_e2e_375d5ca77013_test/stage-three-configuration.log`

## PAGE-077 · service-status.spec.ts:5
- 标题：actual managed service status recovers from read failure and fits narrow layout
- 最新状态：passed；范围：real-api-with-local-or-page-stubs
- 证据：`.local/team-workspace/platform-expansion/qa/owner_agent_e2e_375d5ca77013_test/stage-two-remaining.log`

## PAGE-078 · task-entry.spec.ts:16
- 标题：task and agent pages show lists first and create/edit in accessible dialogs
- 最新状态：passed；范围：real-api-with-local-or-page-stubs
- 证据：`.local/team-workspace/platform-expansion/qa/owner_agent_e2e_375d5ca77013_test/stage-one-browser.log`

## PAGE-079 · task-entry.spec.ts:58
- 标题：real chat tools create one paused schedule, card enables it and the worker executes once
- 最新状态：passed；范围：real-api-local-model
- 证据：`.local/team-workspace/platform-expansion/qa/owner_agent_e2e_375d5ca77013_test/stage-one-routing-fixed.log`

## PAGE-080 · teams.spec.ts:6
- 标题：actual published team configuration, independent worker, roles and tokens
- 最新状态：passed；范围：real-api-with-local-or-page-stubs
- 证据：`.local/team-workspace/platform-expansion/qa/owner_agent_e2e_375d5ca77013_test/stage-one-browser.log`

## PAGE-081 · workflow-branches.spec.ts:44
- 标题：structured schema, iteration and parallel edit through the Chinese canvas
- 最新状态：passed；范围：real-api-with-local-or-page-stubs
- 证据：`.local/team-workspace/platform-expansion/qa/owner_agent_e2e_375d5ca77013_test/stage-one-browser.log`

## PAGE-082 · workflow-branches.spec.ts:177
- 标题：fork and edited branch preserve source history and avoid external calls
- 最新状态：passed；范围：real-api-with-local-or-page-stubs
- 证据：`.local/team-workspace/platform-expansion/qa/owner_agent_e2e_375d5ca77013_test/stage-one-complete-profile.log`

## PAGE-083 · workflow-forms.spec.ts:8
- 标题：structured forms validate fields, preserve JSON and follow the selected published schema
- 最新状态：passed；范围：real-api-with-local-or-page-stubs
- 证据：`.local/team-workspace/platform-expansion/qa/owner_agent_e2e_375d5ca77013_test/stage-one-browser.log`

## PAGE-084 · workflow-loops.spec.ts:22
- 标题：Chinese conditional loop, real error outlet drawing and visible round evidence
- 最新状态：passed；范围：real-api-with-local-or-page-stubs
- 证据：`.local/team-workspace/platform-expansion/qa/owner_agent_e2e_375d5ca77013_test/stage-one-browser.log`

## PAGE-085 · workflow-node-debug.spec.ts:10
- 标题：start node has a direct isolated test action and visible results
- 最新状态：passed；范围：mock-only
- 证据：`.local/team-workspace/platform-expansion/qa/mock-browser-full.log`

## PAGE-086 · workflow-node-debug.spec.ts:10
- 标题：end node has a direct isolated test action and visible results
- 最新状态：passed；范围：mock-only
- 证据：`.local/team-workspace/platform-expansion/qa/mock-browser-full.log`

## PAGE-087 · workflow-node-debug.spec.ts:10
- 标题：llm node has a direct isolated test action and visible results
- 最新状态：passed；范围：mock-only
- 证据：`.local/team-workspace/platform-expansion/qa/mock-browser-full.log`

## PAGE-088 · workflow-node-debug.spec.ts:10
- 标题：agent node has a direct isolated test action and visible results
- 最新状态：passed；范围：mock-only
- 证据：`.local/team-workspace/platform-expansion/qa/mock-browser-full.log`

## PAGE-089 · workflow-node-debug.spec.ts:10
- 标题：rag node has a direct isolated test action and visible results
- 最新状态：passed；范围：mock-only
- 证据：`.local/team-workspace/platform-expansion/qa/mock-browser-full.log`

## PAGE-090 · workflow-node-debug.spec.ts:10
- 标题：mcp node has a direct isolated test action and visible results
- 最新状态：passed；范围：mock-only
- 证据：`.local/team-workspace/platform-expansion/qa/mock-browser-full.log`

## PAGE-091 · workflow-node-debug.spec.ts:10
- 标题：http node has a direct isolated test action and visible results
- 最新状态：passed；范围：mock-only
- 证据：`.local/team-workspace/platform-expansion/qa/mock-browser-full.log`

## PAGE-092 · workflow-node-debug.spec.ts:10
- 标题：condition node has a direct isolated test action and visible results
- 最新状态：passed；范围：mock-only
- 证据：`.local/team-workspace/platform-expansion/qa/mock-browser-full.log`

## PAGE-093 · workflow-node-debug.spec.ts:10
- 标题：transform node has a direct isolated test action and visible results
- 最新状态：passed；范围：mock-only
- 证据：`.local/team-workspace/platform-expansion/qa/mock-browser-full.log`

## PAGE-094 · workflow-node-debug.spec.ts:10
- 标题：subworkflow node has a direct isolated test action and visible results
- 最新状态：passed；范围：mock-only
- 证据：`.local/team-workspace/platform-expansion/qa/mock-browser-full.log`

## PAGE-095 · workflow-node-debug.spec.ts:10
- 标题：iteration node has a direct isolated test action and visible results
- 最新状态：passed；范围：mock-only
- 证据：`.local/team-workspace/platform-expansion/qa/mock-browser-full.log`

## PAGE-096 · workflow-node-debug.spec.ts:10
- 标题：parallel node has a direct isolated test action and visible results
- 最新状态：passed；范围：mock-only
- 证据：`.local/team-workspace/platform-expansion/qa/mock-browser-full.log`

## PAGE-097 · workflow-node-debug.spec.ts:10
- 标题：loop node has a direct isolated test action and visible results
- 最新状态：passed；范围：mock-only
- 证据：`.local/team-workspace/platform-expansion/qa/mock-browser-full.log`

## PAGE-098 · workflow-node-debug.spec.ts:10
- 标题：approval node has a direct isolated test action and visible results
- 最新状态：passed；范围：mock-only
- 证据：`.local/team-workspace/platform-expansion/qa/mock-browser-full.log`

## PAGE-099 · workflow-node-debug.spec.ts:10
- 标题：code node has a direct isolated test action and visible results
- 最新状态：passed；范围：mock-only
- 证据：`.local/team-workspace/platform-expansion/qa/mock-browser-full.log`

## PAGE-100 · workflow-studio.spec.ts:16
- 标题：canvas creates, inserts, copies, deletes, saves and tests nodes with immutable run snapshots
- 最新状态：passed；范围：real-api-with-local-or-page-stubs
- 证据：`.local/team-workspace/platform-expansion/qa/owner_agent_e2e_375d5ca77013_test/stage-one-browser.log`

## PAGE-101 · workflow-studio.spec.ts:93
- 标题：a real local model request shows running node and live completion without replay
- 最新状态：passed；范围：real-api-with-local-or-page-stubs
- 证据：`.local/team-workspace/platform-expansion/qa/owner_agent_e2e_375d5ca77013_test/stage-one-browser.log`

## PAGE-102 · workspace.spec.ts:30
- 标题：private projects, literal search, pin, move, dialogs and retained attachment
- 最新状态：passed；范围：real-api-with-local-or-page-stubs
- 证据：`.local/team-workspace/platform-expansion/qa/owner_agent_e2e_375d5ca77013_test/stage-one-browser.log`

## PAGE-103 · workspace.spec.ts:254
- 标题：knowledge evidence diagnostics and saved keyword threshold on the actual page
- 最新状态：passed；范围：real-api-with-local-or-page-stubs
- 证据：`.local/team-workspace/platform-expansion/qa/owner_agent_e2e_375d5ca77013_test/stage-one-browser.log`

## 默认库存外的三条原生串行用例

- workflow-task-start.spec.ts：通过，17.2秒。独立站点零模型固定流程等待审批。
- workflow-task-resume.spec.ts：通过，37.1秒。本轮四后台真实重启后双审批恢复，代码计数器1。
- operations-backup-native.spec.ts：QA角色未执行；BUG-EXP-007修复独立审查、18项负向与实际8139拒绝、新构建中文文案均通过。默认持久profile真实正向备份由根协调执行，结果待根更新；QA没有操作生产服务。

上述三条不计入默认103分母。

## 默认清单之外的最终补充

默认103条的分母及101通过/2隐藏跳过不变。另行串行的workflow-task-start/resume两项均在独立站点通过；operations-backup-native在正式持久profile原断言/原150及180秒限制下1通过49.9秒，归档、隔离恢复一致及四后台恢复通过，证据qa/production-backup-native.log。BUG007中文提示额外全mock页面1通过1.3秒。并非默认104/106全真实页面；各证据层级保持。
