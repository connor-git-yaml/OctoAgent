# F151 Stage command / path availability manifest

本清单防止phase gate引用未来文件或已删除path。exact argv定义在`testing-matrix.md`；本表的`command_id`是唯一引用，gate解析该文件的反引号argv并验证selector在`producer_task <= stage_task`时已存在且`--collect-only` selected count>0。文件不存在不得skip。

```yaml
version: 1
base_sha: 9d5e1e48691c5ae5a12b33f224d64ac03d5442fc
stages:
  - stage_task: T005
    commands: [C20-recover]
    sdk_state: present
    hard_preconditions: [T005_CORRECTIVE_RED_REVIEW accepted, main supplied corrective aggregate sha256, main supplied nonempty unique --main-review-message-id, rejected v1/index/run bytes unchanged]
    recovery: monotonic reentrant R0-R4 exact path/hash state machine; same argv only; unknown mixed state fails without mutation
    exact_output: .specify/features/151-runtime-boundary-architecture-truth/evidence/evidence-index.v2.json
    prevalidation_or_unknown_state_failure_write_count: 0
    declared_interrupt_result: one exact resumable FSM state
  - stage_task: T006
    commands: [C20-amend]
    sdk_state: present
    hard_preconditions: [both T006 corrective REDs accepted, main supplied exact canonical combined RED aggregate sha256, main supplied one nonempty unique --main-review-message-id, canonical v2 has exactly 20 frozen records and 12 canonical formal runs]
    exact_output: .specify/features/151-runtime-boundary-architecture-truth/evidence/evidence-index.v2.json
    append_only: positions 1-20 byte-identical; one replace appends dirty RED at 21 and index-integrity RED at 22; GREEN later 23/24 and REFACTOR later 25/26 in the same slice order
    prevalidation_or_unknown_state_failure_write_count: 0
  - stage_task: T012
    commands: [C09, C10]
    sdk_state: present
    hard_preconditions: [this corrective GATE_DESIGN approved, this corrective GATE_TASKS approved, dependency resolver RGR and fresh semantic unresolved=0 accepted, record33 remains chain-required and release-excluded]
    required_rgr_order: [S012-dependency-selector-semantics RED, S012-dependency-selector-semantics GREEN, S012-dependency-selector-semantics REFACTOR, S012-standard-backend-scaffold RED, fresh S011 successor direct RED, S012-import-classification-inventory RED, S012-child-isolation-observation RED, S012-standard-backend-scaffold GREEN, S012-import-classification-inventory GREEN, S012-child-isolation-observation GREEN, S012-standard-backend-scaffold REFACTOR, S012-import-classification-inventory REFACTOR, S012-child-isolation-observation REFACTOR, S011-clean-wheel GREEN, S011-clean-wheel REFACTOR]
    test_code_review: {clean_wheel_exact_nodes: 8, predecessor_existing_symbol_count: 32, predecessor_ast_aggregate_sha256: 244ccc7c0b466d71b8affbc5c44a93679daece42209c14a0430e21d054f689a7, predecessor_contract_on_rewrite: SUPERSEDED_HISTORY_NOT_RELEASE_EVIDENCE, standard_backend_node_and_helpers: immutable}
    allowed_implementation_paths: [repo-scripts/check-runtime-architecture.py, octoagent/pyproject.toml, octoagent/uv.lock, repo-scripts/check-clean-wheel.py]
    standard_backend_implementation_paths: [octoagent/pyproject.toml, octoagent/uv.lock, repo-scripts/check-clean-wheel.py]
    dependency_resolver_state: accepted RED/GREEN/REFACTOR; fresh machine semantic revalidation unresolved=0
    standard_backend_precondition: accepted standard-backend RED; dependency resolver REFACTOR accepted; fresh unresolved=0; corrective Gate approved; two new REDs and fresh S011 successor direct RED accepted
    s011_green_precondition: fresh successor direct RED + standard-backend/import-classification/child-observation GREEN/REFACTOR accepted
    shared_venv_precondition: root dev dependency exact hatchling==1.29.0 reviewed; standard uv lock result reviewed; main-owned one-time offline scaffold only
    runtime_product_code_diff_count: 0
    provider_gateway_manifest_diff_count: 0
    standard_backend: {backend: hatchling.build, call: hatchling.build.build_wheel, workspace_wheel_count: 9, metadata_source: real wheel archive}
    standard_installer: {command: uv pip install, offline: true, no_deps: true, target: transaction-local, wheel_source: transaction-local-only}
    isolation: {producer: same real external-cwd import child, observed: [cwd, ordered sys.path, exact env, site and user-site, prefix and base_prefix, workspace origins], uv_cache: transaction-local, home: transaction-local, xdg: transaction-local, tmp: transaction-local, child_pythonpath: isolated-target-only, host_cache_or_home_fallback: reject, source_or_editable_origin: reject, parent_inference: reject}
    preliminary_dependency_contract: {manifest_equals_real_wheel_metadata: true, scan_universe: evaluated distribution installed files only, contexts: [runtime-required, optional-lazy, type-checking, test-plugin, workspace-owned], current_delta_reported: exact, final_verdict: null, final_owner: T070}
    replacement_red_migration: {old_aggregate: 2405581c676b28dea56f9ca66b13e22abeafb386e75ea680e03f246226db84da, old_binding: 61047efa7dfeadb6d20f45664a1cd83e825356b11c460d8958ccc9963139f061, old_state_after_test_rewrite: SUPERSEDED_HISTORY_NOT_RELEASE_EVIDENCE, successor: main-owned exact protocol with fresh raw/review/binding, reuse_old_root_or_review: reject}
    batch_authorization_boundary: test rewrite -> fresh S011 successor RED plus two new S012 REDs -> checker/scaffold implementation -> three S012 GREEN -> three S012 REFACTOR -> S011 GREEN/REFACTOR; continuous only while reversible, hermetic, local and machine-scoped
    forbidden: [manual wheel or zip writer, synthetic METADATA or RECORD, source tree copy, uv sync, command_gateway_full, command_all, full or all parser branch]
    negative_controls: [prefix-only selector acceptance, absent selector required nonempty, wrong group/pin, lock drift/closure missing, alias/duplicate/inverted/unowned semantic key, early S011 GREEN, pin missing or drift, hatchling runtime dependency leak, backend mismatch, manual builder, host state fallback, source or editable leakage, target-wide file scan, installed availability as import evidence, unknown or ambiguous context, parent-inferred child environment, hidden manifest delta, locked scaffold absent fake PASS, reuse of superseded RED root review or binding]
  - stage_task: T014
    commands: [C06-early, C20-pre]
    allowed_files:
      - {path: octoagent/tests/gate/test_runtime_architecture.py, producer: T001}
      - {path: octoagent/tests/gate/test_clean_wheel_contract.py, producer: T011}
      - {path: octoagent/tests/gate/test_lane_orchestrator.py, producer: baseline}
      - {path: octoagent/tests/gate/test_check_changed_lines_coverage.py, producer: baseline/T007}
    forbidden_future_files: [octoagent/tests/gate/test_f151_ci_wiring.py, octoagent/tests/integration/test_f151_runtime_boundary_flow.py, octoagent/tests/integration/test_f151_gateway_startup_fail_closed.py, octoagent/tests/integration/test_update_workspace_safety.py]
  - stage_task: T029
    commands: [C01, C02, C09, C10, C12-pre, C15-pre, C19-pre, C20-pre]
    sdk_state: present
    testpath_count: 10
    atomic_snapshot: {base_replay: required, normalized_source_ast: required, projected_target_snapshot: required, approved_exceptions: 3, embedded_business_hunk: reject}
  - stage_task: T036
    commands: [C21, C08-safety, C15-pre, C19-pre, C20-pre]
    allowed_files:
      - {path: octoagent/tests/integration/test_update_workspace_safety.py, producer: T031}
    forbidden_future_files: [octoagent/tests/integration/test_f151_runtime_boundary_flow.py, octoagent/tests/integration/test_f151_gateway_startup_fail_closed.py]
  - stage_task: T049
    commands: [C03, C03-retired-behavior, C07, C09, C10, C13, C16, C17, C19-post, C20-post]
    sdk_state: retired
    testpath_count: 9
    clean_wheel_level: preliminary only; C11/full/all forbidden
  - stage_task: T070
    commands: [C04, C08-execution, C08-startup, C22, C11, C13, C14, C15-post, C19-post, C20-post]
    clean_wheel_precondition: S070-clean-wheel-full and S070-direct-dependency-closure RGR complete after T017-T029/T023/T045/T049/T064
    allowed_files:
      - {path: octoagent/tests/integration/test_f151_runtime_boundary_flow.py, producer: T063}
      - {path: octoagent/tests/integration/test_f151_gateway_startup_fail_closed.py, producer: T064}
  - stage_task: T084
    commands: [C084, C20-post]
    constructor_behavior: {owner_paths: 44, file_selectors: 42, node_selectors: 3, exact_selectors: 45, skipped_constructor_target: 0, selected: ">0", skip: 0, error: 0, rerun: 0}
    manifest_inputs: [runtime-test-constructors.v1.json, agent-context-test-constructors.v1.json, provider-test-rehome.v1.json]
  - stage_task: T090
    commands: [C05, C084, C07, C08-execution, C08-startup, C14, C15-post, C19-post, C20-post]
  - stage_task: T102
    commands: [C06-final, C20-post]
    allowed_files:
      - {path: octoagent/tests/gate/test_f151_ci_wiring.py, producer: T100}
  - stage_task: T105
    commands: [C06-final, C12-post, C13, C14, C15-post, C19-post, C20-post]
  - stage_task: T120
    commands: [C01-post, C02-post, C03, C03-retired-behavior, C04, C05, C06-final, C07, C08-safety-post, C08-execution, C08-startup, C11, C12-post, C13, C14, C15-post, C16, C17, C20-post, C21-post, C22, C084]
    sdk_state: retired
    command_count: 22
  - stage_task: T121
    commands: [C23]
    sdk_state: retired
    testpath_count: 9
    pytest_shape: {marker: "not real_llm", workers: auto, dist: loadgroup}
    external_access: {network: forbidden, host_credentials: forbidden, external_cost: forbidden, implicit_authorization_from_home_credentials_or_skip: forbidden}
  - stage_task: T122
    commands: [C19-post, C16]
    sdk_state: retired
    exact_environment: {F151_COVERAGE_STAGE: T122}
    coverage_freshness: {stage: T122, start_utc: required, start_head_sha: required, start_head_tree: required, start_worktree_fingerprint: required, reuse_T105: reject}
  - stage_task: T123
    commands: [C24]
    components: [clean-wheel-all, architecture-all, benchmark-two-exact-nodes, frontend-full-vitest, frontend-tsc]
  - stage_task: T124
    commands: [C25]
    prerequisites: [T120, T121, T122, T123, T124 input closure]
    excluded_self_prerequisites: [T124 completion, C25 success, verification-report.md]
    exact_output: .specify/features/151-runtime-boundary-architecture-truth/verification-report.md
    final_required_committed_paths: 4
```

## Bootstrap / evidence path states

- T001-T004 RED时`tdd-evidence` runner/checker尚不存在；只使用`rgr-slices.md`定义的标准pytest/Vitest Phase0 bootstrap transaction，按[`artifact-lifecycle.v1.json`](artifact-lifecycle.v1.json)的6个exact bootstrap slice/type/path保存JUnit/stdout/stderr/exit/invocation/tree。
- T004完成后状态必须为`PHASE0_RED_REVIEW`并硬停。main读取真实artifact/tree diff后创建唯一`.specify/features/151-runtime-boundary-architecture-truth/evidence/bootstrap-anchor.v1.json`，并在放行消息中提供该文件的64位小写SHA256；聊天文字不是机器输入，exact file+`--bootstrap-anchor-sha256`才是。
- 原`C20-bootstrap`已消费anchor并生成被拒绝v1 index，不能重放或作为release evidence。纠正测试先复用RGR manifest现有S004/S002 nodeids，以标准pytest/JUnit transaction写exact corrective-red六件套并硬停；它不是第二runner，也不修改anchor/36 raw/旧index/旧runs。
- main接受corrective RED后才可运行`C20-recover`。该命令显式消费immutable anchor SHA、rejected v1 SHA、main提供的corrective aggregate SHA与`--main-review-message-id`；review ID不得从env/default/聊天推断，不得为空、未知或复用anchor/rejected/T006消息，并与aggregate形成canonical approval binding。恢复按lifecycle的R0-R4 exact path/hash状态单向推进：runs rename、v1 rename、v2 temp write/fsync/replace任一步中断后以相同argv重入；只清理hash不完整的recovery-owned temp，其他未知混合态0写失败。它不声称跨多个路径原子或可rollback。
- bootstrap、corrective与formal的`tree.json`统一使用与immutable bootstrap raw兼容的12字段exact schema；record逐字段交叉`slice/phase/base_ref/merge-base/head/tree/fingerprint scope/files/status_porcelain/captured_utc`。v2固定前缀为6条bootstrap RED后紧接2条corrective RED，identity key为`lifecycle_type+task_id+slice_id+phase`；rejected v1 record进入链即失败。
- C20正式语法为`tdd-evidence verify --mode <local-working-tree|committed> --base-ref <ref> --evidence-index <exact-v2-path> [--through-task <id>]`；formal run唯一语法为`tdd-evidence run --slice <id> --phase <RED|GREEN|REFACTOR> --mode local-working-tree --base-ref <ref> --evidence-index <exact-v2-path>`。formal output固定为`evidence/local/runs/<slice>/<phase>/`六件套，runner不接受output root/filename override。mode/base/task任一未解析、未使用或无效都失败。
- C20-amend不是第二subcommand：只给既有`run`的RED分支增加`--adopt-corrective-red-root`、`--corrective-red-aggregate-sha256`与`--main-review-message-id`。输入root必须machine-equal T006 lifecycle exact root，不是caller output override；RED record追加后同一参数重入不得重复写。normal RED/任一GREEN/REFACTOR不接受这三个参数。
- local mode changed set=committed base→HEAD + staged + unstaged + untracked final behavior paths；dirty但HEAD不变绝不能0 required slices PASS。CI clean tree使用committed mode。
- committed mode复用fingerprint scope；除exact generated evidence outputs与canonical anchor/index外，任一porcelain staged/unstaged/untracked状态在base diff前以`EVIDENCE_COMMITTED_WORKTREE_DIRTY`失败。production-only、tracked-only、CI-only放宽均禁止。

## Mechanical self-check nodes

- `TestStageCommandManifest::test_each_selector_is_produced_before_stage_and_collects_nonzero`
- `TestStageCommandManifest::test_pre_sdk_coverage_has_ten_paths_and_post_sdk_has_nine`
- `TestStageCommandManifest::test_stage_commands_reject_future_or_retired_paths`
- `TestStageCommandManifest::test_c03_uses_exact_nodes_and_cannot_deselect_zero`
- `TestStageCommandManifest::test_t120_t121_t122_t123_and_t124_have_frozen_env_cwd_paths_markers_outputs_and_components`
- `TestStageCommandManifest::test_f151_automatic_verify_rejects_live_credentials_external_cost_and_implicit_authorization`
- `TestStageCommandManifest::test_t121_t123_and_c084_have_frozen_env_paths_markers_and_counts`
- `TestStageCommandManifest::test_coverage_transaction_creates_report_parent_before_checker`
- `TestStageCommandManifest::test_t122_coverage_is_fresh_for_its_own_start_tree_and_cannot_reuse_t105`
- `TestManifestIntegrity::test_evidence_producer_paths_names_and_lifecycle_sets_are_bijective`
- `TestManifestIntegrity::test_committed_artifacts_have_reachable_first_writers_and_producer_commands`
- `TestStageCommandManifest::test_pre_and_post_sdk_profiles_cover_every_command_without_retired_path_leak`
- `TestArtifactLifecycle::test_unknown_or_wrong_phase_evidence_path_fails_even_when_gitignored`
- `TestTddEvidence::test_bootstrap_anchor_rejects_missing_malformed_replaced_mixed_or_second_anchor`
- `TestManifestIntegrity::test_phase2_scope_never_requires_phase3_or_phase4_evidence`
- `TestManifestIntegrity::test_phase3_scope_never_requires_phase4_evidence`
- `TestManifestIntegrity::test_declared_new_path_without_slice_owner_still_fails`
- `TestManifestIntegrity::test_declared_new_paths_are_absent_in_base_tree`
- `TestManifestIntegrity::test_atomic_namespace_snapshot_rejects_t029_business_change_and_requires_later_slice_evidence`

negative fixtures分别证明：T014引用T100文件失败、T036引用T063/T064文件失败、post-retirement传SDK path失败、pre-retirement漏SDK失败、T120出现pre-SDK command失败、T121出现C18/`lane.py baseline`/live或credential-bearing命令失败、从默认HOME/凭证存在/skip推定授权失败、T122复用T105或stage/start tree/UTC不符失败、exact node selected=0失败、C084 owner集合不是44、selector不是42 files+3 exact nodes=45、F033任一构造点仍skip、helper无reverse-call、selected=0或出现skip/error/rerun均失败、report parent未创建失败、formal `.bin`/`run.json`/noncanonical root/缺invocation或tree失败、committed path无reachable producer失败、T049缺C09/C10或提前出现C11/full/all失败、T070缺C11或没有完整S070 evidence失败、T049真实Phase2 diff不要求Phase3/4 evidence、T070真实Phase3 diff不要求Phase4 evidence、同shared subgroup漏任一slice失败、broad/declared-new但无slice owner仍失败、base tree已存在path误标declared-new失败、T029夹带业务hunk失败、T029后有授权slice evidence通过且无slice失败；另外验证12字段tree exact/schema cross-check、v2八条固定前缀、C20-recover缺失/空/未知/复用review ID、R1/R2/partial-temp/R3/R4重入与未知混合态、`architecture all`有效ref正向和missing/unresolvable ref负向、finalize不自依赖且失败0写。
