# F151 Operations deterministic test-owner manifest

本表覆盖D-03的33个backing modules，并区分源码事实与完成态。baseline以worktree PYTHONPATH锁执行`--collect-only`：候选24文件收集260 tests、collection error=0；T035补齐并执行7个缺失exact nodes后，当前完成态为verified owner 33/33、scheduled/planned=0、fake-only=0、unknown=0。`planned`绝不等于covered。

## 1. Owner mode与机械判定

- `direct`：指定collectable node的测试AST直接import目标模块/production symbol，并实际调用/构造它；oracle验证生产结果或持久化副作用。
- `indirect`：node通过另一真实production root到达目标；必须冻结调用链与target verdict，不能用fake替换target。
- `declarative`：只验证manifest/schema/monkeypatch target，或把production target替换成fake；不算行为owner。
- `scheduled`：完成态要新增/迁移的owner；在对应task前不算covered，Verify必须归零。

Gate解析test AST/import、运行`pytest --collect-only`并核对生产调用；不存在node、未collect、表格伪direct、只mock自身、planned冒充covered均失败。高风险store、durable audit、更新/凭据安全边界必须direct L4。

## 2. 33/33 inventory

| module / role tag | baseline truth | 完成态 owner、phase与oracle |
|---|---|---|
| `backup_audit.py` / store | indirect：`test_backup_service.py::test_create_bundle_excludes_plaintext_secrets_and_updates_state`→`BackupService.create_bundle`→recorder，仅STARTED/COMPLETED | T034 `test_backup_audit.py::test_recorder_roundtrips_started_completed_failed_events`、`::test_recorder_retry_is_idempotent_across_instances`、`::test_recorder_store_error_leaves_no_partial_event`，direct L4 RGR，oracle为event顺序/幂等/事务 |
| `backup_service.py` / application | direct | 迁移后同名owner；manifest、secret exclusion、custom data-dir result |
| `channel_verifier.py` / application | direct | `test_channel_verifier.py::test_registry_register_and_get`；registry/result/missing builder，无HTTP oracle |
| `chat_import_service.py` / application | direct | `test_chat_import_service.py::test_chat_import_persists_artifact_fragment_and_fact_commit` |
| `control_plane_models.py` / domain | indirect | T035 verified direct：`test_operations_models.py::test_control_plane_models_roundtrip_and_reject_invalid_payload`；serialization/required field/mutable-default oracle |
| `doctor.py` / application | direct | `test_doctor.py::TestDoctorChecks::test_python_version_pass`及doctor checks |
| `doctor_remediation.py` / application | direct | `test_doctor_remediation.py::test_planner_builds_blocking_guidance`；Rich renderer不在此owner |
| `import_mapping_store.py` / store | indirect：workbench service default store | T035 verified direct：`test_import_workbench_service.py::test_import_mapping_store_roundtrip_corruption_and_independent_instances` |
| `import_source_store.py` / store | indirect | T035 verified direct：`test_import_workbench_service.py::test_import_source_store_roundtrip_atomic_and_corruption` |
| `import_workbench_models.py` / domain | direct partial | 同文件model validation + service persisted document；direct symbol coverage |
| `import_workbench_service.py` / application | direct | `test_import_workbench_service.py::test_import_workbench_detect_preview_run_and_resume` |
| `models.py` / domain | direct scattered | T024 rehome为`test_operations_models.py::test_doctor_models_roundtrip_and_unused_credential_import_is_absent`，避免与Provider test_models混名 |
| `onboarding_models.py` / domain | direct | `test_onboarding_models.py::test_command_action_requires_command` |
| `onboarding_service.py` / application | direct | `test_onboard.py::test_onboarding_service_happy_path`；fake doctor/store/clock + persisted outcome |
| `onboarding_store.py` / store | direct | `test_onboarding_store.py::test_store_roundtrip`及corruption/atomic |
| `project_migration.py` / application | direct | `test_project_migration.py::test_migration_apply_creates_default_project_for_empty_instance`；保留F094 |
| `project_selector.py` / application | direct partial | rehome existing project command/session characterization |
| `recovery_status_store.py` / store | direct | `test_recovery_status_store.py::test_roundtrip_latest_backup_and_recovery_drill` |
| `runtime_descriptor_defaults.py` / application | direct current仅legacy normalize | T031 L4 DI fake：三个`test_update_preflight_rejects_*_before_destructive_commands`；L3真实Git只在`tests/integration/test_update_workspace_safety.py::test_real_git_repo_*_is_untouched_and_returns_local_changes_present`，不得标L4 |
| `secret_models.py` / domain | direct partial：`SecretRef`/`SecretAuditReport` | T024 `test_operations_models.py::test_secret_models_never_serialize_or_repr_raw_secret_and_defaults_are_independent`；不得引用Provider credential test |
| `secret_refs.py` / adapter | direct但当前exec启真实hermetic subprocess | T030注入runner后`test_secret_refs.py::test_exec_reference_uses_injected_runner_without_host_subprocess`；env/file/keyring均DI fake |
| `secret_service.py` / application | direct | `test_secret_service.py::test_secret_service_configure_audit_apply_and_unmanaged_reload` |
| `secret_status_store.py` / store | indirect | T035 verified direct：`test_secret_service.py::test_secret_status_store_roundtrip_corruption_and_atomic_replace` |
| `service_manager.py` / adapter | direct | `test_service_manager.py::TestInstallIdempotency::test_missing_installs_and_activates`；DI subprocess result |
| `setup_governance_adapter.py` / adapter | declarative/fake-only | T035 verified direct：`test_setup_governance_adapter.py::test_real_adapter_handles_health_error_malformed_envelope_and_existing_profile_without_default_write`；MockTransport只位于HTTP边界，执行production adapter与client |
| `sleep_probe.py` / adapter | direct | 正确owner `test_doctor_service_checks.py::TestProbeSleepRisk`（8普通+3参数化）；不是test_service_manager |
| `telegram_pairing.py` / store | direct partial | T032 `test_telegram_pairing.py::test_two_store_instances_preserve_delete_and_offset_under_barrier`；roundtrip/corruption/atomic/concurrent，无sleep |
| `telegram_verifier.py` / adapter | direct | `test_telegram_verifier.py::test_verifier_run_readiness_uses_real_client_and_store`；DI HTTP零网络 |
| `update_service.py` / application | direct partial | T033 `test_update_service.py::test_concurrent_apply_launches_exactly_one_worker`及existing phase owner |
| `update_status_store.py` / store | direct partial | T033 claim/release nodes；roundtrip/corruption/atomic/concurrency |
| `update_worker.py` / adapter | planned无owner | T035 verified direct：`test_update_worker.py::test_update_worker_executes_requested_attempt_with_injected_service`；另由clean-wheel L3 module wiring，不把L3代替L4 |
| `wizard_session.py` / application | direct | `test_wizard_session.py::test_wizard_session_start_resume_status_cancel`；Click driver另属CLI |
| `wizard_session_store.py` / store | indirect | T035 verified direct：`test_wizard_session.py::test_wizard_session_store_roundtrip_corruption_reset_and_backup` |

六个原`direct-planned`模块（control-plane models、mapping store、source store、secret status store、update worker、wizard store）及`setup_governance_adapter`假绿已在T035由上述exact nodes转为verified direct；`backup_audit`已在T034闭合。完成态机械计数为verified owner 33/33、scheduled/planned=0、fake-only=0、unknown=0。

## 3. 分层测试契约

1. Store L4：tmp path/SQLite、独立实例；roundtrip/corruption/atomic；有RMW/claim再用Barrier/Event做并发，禁sleep。
2. Application L4：subprocess/HTTP/store/clock/credential使用DI fake，oracle包含业务持久化结果，不只断言fake call。
3. Adapter L4：调用真实production adapter，外部边用injected runner/MockTransport；只monkeypatch替换adapter不算owner。
4. CLI L4：CliRunner只测参数/exit/output/service call，不复制下层规则。
5. L3：clean-wheel验证CLI/update-worker/routes wiring；`runtime_descriptor_defaults`真实tmp Git为L3；EventStore→REST为L3。L1/L2新增0。

## 4. Gate negative fixtures

- `test_test_owner_rejects_nonexistent_or_uncollected_node`
- `test_test_owner_rejects_declared_direct_without_ast_import_and_production_call`
- `test_test_owner_rejects_fake_only_or_mock_self_verification`
- `test_test_owner_requires_indirect_call_chain_and_target_verdict`
- `test_test_owner_requires_scheduled_count_zero_at_verify`
- `test_test_owner_requires_direct_high_risk_store_and_durable_audit`

verification report必须输出`verified_direct/verified_indirect/declarative/scheduled/unknown`；Verify目标为33/33有verified owner、declarative=0（作为唯一owner时）、scheduled=0、unknown=0。

## 5. Runtime test-definition owner exception

`test_task_service_context_integration.py` baseline有两个同名顶层`test_task_service_prompt_context_only_exposes_sanitized_control_metadata`；pytest只collect后一定义，前一包含的TaskService constructor与LLM override是dead-shadowed，不能算owner或live migration点。T084/S084-shadowed-test先保存collect-only absence/presence证据，再把前一定义改名为`test_worker_tool_writeback_and_private_memory_are_isolated_across_sessions`，要求两个exact nodes均collect/pass。Gate全tests扫描duplicate top-level/class test qualname；baseline仅此1组，完成态0组。
