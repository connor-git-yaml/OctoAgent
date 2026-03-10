# Verification Report: Feature 036 Guided Setup Governance

## 状态

- 阶段：Phase 1 与 Phase 2（部分）已实现
- 日期：2026-03-10

## 本次验证内容

1. 已在 control-plane canonical 体系下新增：
   - `setup-governance`
   - `policy-profiles`
   - `skill-governance`
2. 已把上述资源接入 `GET /api/control/snapshot` 与 `GET /api/control/resources/*`，不新增平行 backend。
3. 已实现 `setup.review`，可基于当前状态和 draft config/profile/policy 输出统一风险摘要、阻塞项与 next actions。
4. 已实现 `agent_profile.save`，并把 project 默认主 Agent profile 绑定到 `Project.default_agent_profile_id`。
5. 已实现 `policy_profile.select`，并把选择结果写入 `Project.metadata.policy_profile_id`，同时同步 `PolicyEngine` 的运行态 profile。
6. 已扩 control-plane `config.ui_hints`，把 `front_door.*`、Telegram `dm_policy/group_policy/group_allow_users` 等安全字段显式暴露到图形化设置路径。
7. 已执行后端回归：`uv run --group dev pytest apps/gateway/tests/test_control_plane_api.py -q`，结果 `26 passed in 7.68s`。

## 本次未完成

- 尚未实现 `setup.apply`
- 尚未实现 `skills.selection.save`
- 尚未把 `build_config_schema_document()` 与 CLI `octo init / octo onboard` 收敛到同一 setup/review/apply 主链
- 尚未接 035 `SettingsCenter / Home` 的图形化消费层
- 尚未补 frontend / CLI / e2e 测试矩阵

## 当前剩余硬门禁

- 必须补 `setup.apply`，并明确对 `skills selection` 的真实持久化策略，避免只改显示不改运行边界。
- 必须补 CLI integration tests，证明 `octo init / octo onboard` 消费同一 review/apply 语义。
- 必须补 frontend integration tests，证明 035 Settings/Setup 不再直接拼生资源。
- 必须补更细的 secret redaction tests，覆盖 `setup.review` / `policy_profile.select` 相关事件与结果。
