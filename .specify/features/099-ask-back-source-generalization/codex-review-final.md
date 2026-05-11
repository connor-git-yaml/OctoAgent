# F099 Final Cross-Phase Adversarial Review
Date: 2026-05-11
Reviewer: Codex (adversarial)
Commits reviewed: 1dbade4 519a569 f4651dc 8884cc4 7ff450c

## Domain Results

### Domain 1 — F098 OD-1~OD-9 Compliance
CLEAN with one downstream caveat tracked in Domain 4. The three new tools do not emit USER_MESSAGE for control payloads: `_emit_ask_back_audit()` builds `EventType.CONTROL_METADATA_UPDATED` with `ControlMetadataUpdatedPayload` only (`octoagent/apps/gateway/src/octoagent/gateway/services/builtin_tools/ask_back_tools.py:99`). They also do not import or inherit `BaseDelegation`, so OD-5 shared delegation state is not introduced. The ask_back tools do not call `spawn_child`; the only spawn-related change is metadata injection on existing `delegate_task` / `subagents.spawn` paths, so OD-7's "no new spawn tool/path" constraint is not violated.

Remaining F098 OD constraints show no direct source-level violation in the reviewed diff: no atomic transaction expansion, no target Worker profile bypass, and no new A2AConversation source field.

### Domain 2 — ask_back Edge Cases
NOT CLEAN. `ask_back` / `request_input` assume the live execution context corresponds to a RUNNING task. If the task is PAUSED, COMPLETED, FAILED, or otherwise not RUNNING, `ExecutionConsole.request_input()` still mutates the live session to WAITING_INPUT before checking task state, only writes the Task state transition when `task.status == RUNNING`, then waits on the queue (`execution_console.py:293`, `execution_console.py:303`, `execution_console.py:348`). In that state, `attach_input()` will reject because task.status is not WAITING_INPUT (`execution_console.py:376`). This can leave a pending waiter that the user cannot satisfy.

When `execution_context=None`, audit is skipped (`ask_back_tools.py:83`) and the handler later catches the RuntimeError and returns `""` for ask_back/request_input or `"rejected"` for escalate_permission. Exceptions inside `_emit_ask_back_audit()` are silently swallowed after a warning (`ask_back_tools.py:123`), so the tool can appear successful with no audit record.

### Domain 3 — source_runtime_kind Injection
NOT CLEAN. The implementation no longer uses the exact `deps._execution_context is not None` condition mentioned in the prompt; it now checks `get_current_execution_context().runtime_kind == "worker"` (`_spawn_inject.py:49`). That is better but still unsafe. The owner-self main execution path registers an `ExecutionRuntimeContext` with `runtime_kind = DelegationTargetKind.WORKER.value` before calling the main LLM loop (`orchestrator.py:1356`, `orchestrator.py:1321`). If that owner-self/main Web UI path calls `delegate_task`, `_spawn_inject.py` will inject `source_runtime_kind="worker"` even though the source should default to main unless the dispatch envelope explicitly says otherwise. This reopens the F098 audit-chain class of bug: target-side or execution-mode metadata is being treated as caller identity.

### Domain 4 — Audit Trace Completeness (AC-G4)
NOT CLEAN. AC-G4 says all three tool calls must have `CONTROL_METADATA_UPDATED` audit records with correct `task_id` linkage. The implementation does not guarantee that. `_emit_ask_back_audit()` returns without writing if execution context is absent (`ask_back_tools.py:83`) and catches every append failure (`ask_back_tools.py:123`). All three handlers continue after that path. Therefore an EventStore outage, missing context, or append error produces a successful/normal tool result with no AC-G4 audit event.

The happy-path task_id linkage is correct when context and EventStore both work: the event task_id is taken from `exec_ctx.task_id` and written into the Event (`ask_back_tools.py:81`, `ask_back_tools.py:101`). The failure path is the issue.

### Domain 5 — Completion-Report AC Remapping
NOT CLEAN. The completion report claims `13/13` AC coverage (`completion-report.md:118`), but spec.md §4 contains AC-B1~B5, AC-C1~C3, AC-D1~D2, AC-E1, and AC-G1~G4. AC-B5 and AC-C3 are missing from the report table entirely, and AC-G4 is remapped from "audit trace complete" to "Constitution C6 degradation" (`completion-report.md:116`).

Several PASS rows are not actually validated at the stated acceptance level:
- AC-B2 is marked PASS by a mock `request_input()` call, not by proving `task.status = WAITING_INPUT`, `session.can_attach_input = True`, and TASK_STATE_CHANGED in Event Store.
- AC-B4 / AC-G3 are marked PASS, but production `_approval_gate` is not injected and the handler does not enter WAITING_APPROVAL.
- AC-C1 is marked PASS by testing `_spawn_inject` directly, not by a worker `delegate_task` call flowing through dispatch resolution.
- AC-E1 is marked PASS while the three-event sequence test is explicitly tagged `[E2E_DEFERRED]` (`test_phase_e_ask_back_e2e.py:163`).

### Domain 6 — Deferred Items Legitimacy
NOT CLEAN. `_approval_gate` is declared on `ToolDeps` but not wired in production construction (`capability_pack.py:1036`), and `escalate_permission` returns `"rejected"` whenever it sees `approval_gate is None` (`ask_back_tools.py:331`). Marking AC-G3 PASS is therefore incorrect for production behavior.

Even after wiring `_approval_gate`, the current handler only calls `approval_gate.request_approval()` and `wait_for_decision()` (`ask_back_tools.py:361`); it never sets task/session state to WAITING_APPROVAL and never performs a WAITING_APPROVAL → RUNNING transition. User impact today: `worker.escalate_permission` is visible, but in production it immediately denies all requests; after future DI wiring it would still block inside a RUNNING task without exposing the WAITING_APPROVAL state promised by AC-B4/AC-B5/FR-E4.

The `[E2E_DEFERRED]` tag on the three-event sequence makes AC-E1 only partially validated, not PASS. Deferring that test may be acceptable, but the report must not present AC-E1 as complete.

### Domain 7 — Phase D / C Merge
PARTIAL. I did not find a dropped source constant or payload documentation point from merging Phase D work into Phase C/D commits: `source_kinds.py` has the runtime-kind constants and control-metadata source constants, and `payloads.py` documents the new control sources.

Testing is weaker than the completion report states. The Phase C tests validate constants for `main/worker/subagent/automation/user_channel` and explicit resolver branches for `automation` and `user_channel`. They do not validate the older "butler/user/worker/automation" wording from the review prompt; current F099 spec no longer defines `butler` or plain `user` as `source_runtime_kind` values, so I am not filing that as a spec violation. They also do not run an end-to-end worker `delegate_task` dispatch through `_resolve_a2a_source_role`; AC-C1 is covered by helper tests rather than the actual dispatch chain.

## Finding Register

| ID | Severity | File:Line | Description | Recommendation |
|----|----------|-----------|-------------|----------------|
| F1 | HIGH | octoagent/apps/gateway/src/octoagent/gateway/services/orchestrator.py:1356 | Owner-self/main execution registers `runtime_kind="worker"`. `_spawn_inject.py:49` treats that runtime_kind as caller identity and injects `source_runtime_kind="worker"`, so a main Web UI path can be audited as worker source. | Do not infer caller source from execution-mode `runtime_kind` alone. Inject only from an explicit trusted caller-source marker in dispatch metadata/envelope, or explicitly exclude `owner_execution_mode="worker_self"` / owner-self sessions. |
| F2 | HIGH | octoagent/apps/gateway/src/octoagent/gateway/services/capability_pack.py:1036 | Production `ToolDeps` construction does not pass an ApprovalGate. `worker.escalate_permission` therefore sees `_approval_gate is None` and always returns `"rejected"` in production. | Wire the production ApprovalGate into ToolDeps before registering built-ins, or mark `worker.escalate_permission` unavailable until the dependency exists. |
| F3 | HIGH | octoagent/apps/gateway/src/octoagent/gateway/services/builtin_tools/ask_back_tools.py:361 | `escalate_permission` calls ApprovalGate directly but never transitions the task/session to WAITING_APPROVAL, never exposes `approval_id` on the execution session, and never restores WAITING_APPROVAL → RUNNING. This fails AC-B4/AC-B5/FR-E4 even if ApprovalGate is wired. | Route through the existing execution-console approval/input state machine or add explicit TaskService + session transitions with tests for WAITING_APPROVAL and return-to-RUNNING. |
| F4 | MEDIUM | octoagent/apps/gateway/src/octoagent/gateway/services/builtin_tools/ask_back_tools.py:123 | `_emit_ask_back_audit()` swallows every EventStore append failure; line 83 also skips audit entirely without execution_context. The tool can succeed or degrade normally while AC-G4 audit records are absent. | For AC-G4, make audit write failure observable to the tool result or task diagnostics, and add tests for append failure/no-context paths. If graceful degradation is intentional, mark AC-G4 partial instead of PASS. |
| F5 | MEDIUM | octoagent/apps/gateway/src/octoagent/gateway/services/execution_console.py:293 | `request_input()` mutates the live session into WAITING_INPUT before requiring the Task to be RUNNING. For non-RUNNING tasks, task status may remain PAUSED/COMPLETED/FAILED while the handler waits on a queue that `attach_input()` will reject. | Validate `task.status == RUNNING` before creating `PendingInputRequest` / mutating session state, and return a clear rejected/error tool result for non-RUNNING tasks. |
| F6 | MEDIUM | .specify/features/099-ask-back-source-generalization/completion-report.md:100 | Completion report claims `13/13` AC coverage but omits AC-B5 and AC-C3 from spec.md §4 and miscounts the acceptance set. | Rebuild the AC table directly from spec.md §4, include AC-B5 and AC-C3, and downgrade unvalidated rows to PARTIAL/DEFERRED. |
| F7 | MEDIUM | .specify/features/099-ask-back-source-generalization/completion-report.md:112 | AC-E1 is marked PASS even though the actual three-event sequence test is explicitly `[E2E_DEFERRED]` in `test_phase_e_ask_back_e2e.py:163`. | Either implement the TaskRunner + ExecutionConsole integration test for the full three-event sequence, or mark AC-E1 PARTIAL/DEFERRED. |
| F8 | MEDIUM | .specify/features/099-ask-back-source-generalization/completion-report.md:116 | AC-G4 is remapped to "Constitution C6 degradation"; spec AC-G4 is "all three tool calls have CONTROL_METADATA_UPDATED audit records with correct task_id linkage." | Replace the AC-G4 row with the actual audit-trace criterion and add/point to tests covering all three tools, task_id linkage, and failure behavior. |
| F9 | LOW | .specify/features/099-ask-back-source-generalization/handoff.md:64 | Handoff says `source_runtime_kind="subagent"` maps to SUBAGENT/SUBAGENT_INTERNAL, but code maps `"subagent"` through the WORKER/WORKER_INTERNAL branch (`dispatch_service.py:870`). This is a downstream documentation hazard. | Correct the handoff table to match current code or add a real SUBAGENT runtime role if that is the intended future contract. |

## Summary
Total: 3 HIGH / 5 MEDIUM / 1 LOW
Overall verdict: NOT READY as a final PASS. The ask_back happy path is plausible, but source identity injection and `escalate_permission` are not production-correct, and the Verify documentation materially overstates AC coverage.
