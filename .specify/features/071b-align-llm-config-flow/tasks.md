# Tasks

## Slice A - Web Provider `base_url`

- [x] 扩展 `octoagent/frontend/src/domains/settings/shared.tsx` 的 `ProviderDraftItem`、`parseProviderDrafts()`、`stringifyProviderDrafts()`，保证 `base_url` 无损 round-trip
- [x] 更新 `octoagent/frontend/src/domains/settings/SettingsProviderSection.tsx`，在 Provider 卡片中新增 `API Base URL` 输入项与说明
- [x] 增加 `octoagent/frontend/src/domains/settings/SettingsPage.test.tsx`，覆盖自定义 Provider `base_url` 编辑和 `setup.review` draft 提交

## Slice B - Memory alias 前置校验

- [x] 在 `octoagent/packages/provider/src/octoagent/provider/dx/config_schema.py` 中新增 `memory.*_model_alias -> model_aliases` 引用校验
- [x] 在 `octoagent/apps/gateway/src/octoagent/gateway/services/control_plane.py` 中为 `setup.review` 补充 Memory alias 风险提示，区分默认 fallback 与明显错配
- [x] 增加 `octoagent/packages/provider/tests/dx/test_config_schema.py` 和 `octoagent/apps/gateway/tests/test_control_plane_api.py` 的回归测试

## Slice C - 统一入口与文案

- [x] 修正 `octoagent/apps/gateway/src/octoagent/gateway/services/builtin_tools/config_tools.py` 中 `config.sync` / `config.add_provider` / `config.set_model_alias` 的工具说明
- [x] 修正 `octoagent/packages/provider/src/octoagent/provider/dx/config_commands.py` 中 `config` / `config sync` / `provider` / `alias` 的 CLI 描述，明确其只同步衍生配置
- [x] 重写 `skills/llm-config/SKILL.md`，统一 `octoagent.yaml`、Provider / alias / Memory 配置和生效方式说明
- [ ] 更新 `README.md`、`octoagent/README.md`、`docs/blueprint.md` 的相关段落，移除过期的 `litellm-config.yaml` 手改路径与 MemU 三模式说明 _(deferred to M5)_

## Slice D - CLI / Agent 自定义 Provider 闭环

- [x] 在 `octoagent/packages/provider/src/octoagent/provider/dx/config_schema.py` 中补齐 `providers.0.base_url` 的 wizard/uiHints 暴露，并把 memory 纳入 wizard 顺序
- [x] 在 `octoagent/packages/provider/src/octoagent/provider/dx/config_commands.py` 与 `config_wizard.py` 中支持 CLI `base_url` 输入，并避免更新已有 Provider 时静默丢失 `base_url`
- [x] 更新 `octoagent/packages/provider/src/octoagent/provider/dx/wizard_session.py`，让 CLI wizard 收集 `providers.0.base_url` 与 `memory.*_model_alias`
- [ ] 在 `octoagent/apps/gateway/src/octoagent/gateway/services/builtin_tools/` 中新增 Agent `setup.review` / `setup.quick_connect` 高层工具 _(deferred to M5)_
- [ ] 增加 `octoagent/packages/provider/tests/dx/test_config_wizard.py`、`test_wizard_session.py`、`test_project_commands.py`、`octoagent/apps/gateway/tests/test_capability_pack_tools.py` 的回归测试 _(deferred to M5)_

## Slice E - Runtime Alias 架构修正

- [x] 重构 `octoagent/packages/provider/src/octoagent/provider/alias.py`，让 runtime alias registry 以 `octoagent.yaml.model_aliases` 为主事实源，legacy 语义 alias 仅保留兼容 fallback
- [x] 更新 `octoagent/apps/gateway/src/octoagent/gateway/main.py` 与 `services/llm_service.py`，启动时加载配置驱动 alias registry，移除"未知 alias 静默回 main"的主链路
- [x] 在 `octoagent/apps/gateway/src/octoagent/gateway/services/control_plane.py` 中为 `worker_profile.review` 与 `agent_profile.save` 补充 alias 存在性校验
- [x] 更新 `octoagent/frontend/src/domains/agents/agentManagementData.ts` 与相关 AgentCenter UI/测试，去掉硬编码 alias 选项，只暴露真实可用 alias
- [x] 扩展 `octoagent/packages/provider/src/octoagent/provider/dx/cli.py` / `config_bootstrap.py`，让 `octo setup` 支持 custom provider + `base_url` 主路径
- [x] 增加 `octoagent/packages/provider/tests/test_alias.py`、`octoagent/apps/gateway/tests/test_main.py`、`octoagent/frontend/src/pages/AgentCenter.test.tsx` 的回归测试，覆盖 alias 从配置到运行时消费的整链路

## Validation

- [x] `pytest octoagent/packages/provider/tests/dx/test_config_schema.py -q`
- [x] `pytest octoagent/packages/provider/tests/test_alias.py -q`
- [x] `pytest octoagent/apps/gateway/tests/test_control_plane_api.py -q -k "setup_review or setup_governance"`
- [x] `pytest octoagent/apps/gateway/tests/test_main.py -q -k "stream_model_aliases or runtime_alias_registry"`
- [ ] `npm test -- --run src/pages/AgentCenter.test.tsx` _(deferred to M5)_
- [ ] `npm test -- --run octoagent/frontend/src/domains/settings/SettingsPage.test.tsx` _(deferred to M5)_
