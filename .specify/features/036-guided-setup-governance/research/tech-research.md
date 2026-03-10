# Tech Research: Feature 036 — Guided Setup Governance

## 1. 当前代码基线

## 1.1 初始化主链仍然是分裂的

证据：

- `octoagent/packages/provider/src/octoagent/provider/dx/cli.py`
- `octoagent/packages/provider/src/octoagent/provider/dx/onboarding_service.py`
- `octoagent/packages/provider/src/octoagent/provider/dx/wizard_session.py`

现状：

- `octo init` 只驱动 `WizardSessionService`
- `octo onboard` 负责首次使用统一入口
- `OnboardingService._run_provider_runtime()` 在缺配置时主要返回命令型 next actions，例如：
  - `octo config provider add openrouter`
  - `octo config alias set main`
  - `octo config sync`
  - `octo secrets audit`

结论：

- 当前 onboarding 仍然是“汇总命令建议”，不是“可直接 apply 的设置交易”。
- 036 必须把初始化主链升级为共享 draft/review/apply，而不是继续拼命令。

## 1.2 Wizard 实际只覆盖 provider/runtime/telegram 最小字段

证据：

- `wizard_session.py`
- `config_schema.py::build_config_schema_document()`

现状：

- `wizard_order` 只有 `project / provider / models / runtime / telegram / review`
- `_drive_cli()` 实际只采集 provider、model aliases、runtime 和 Telegram webhook/polling
- `front_door`、`dm_policy`、`group_policy`、`group_allow_users`、Agent Profile、权限 preset、Tools / Skills 都不在 wizard 中

结论：

- 015/025 的 wizard contract 需要扩充，否则 036 做出来也接不上首次使用主链。

## 1.3 配置 schema 已有安全字段，但 UI hints 和前端没有真正消费

证据：

- `config_schema.py`
- `control_plane.py::_build_config_ui_hints()`
- `frontend/src/pages/SettingsCenter.tsx`

现状：

- `OctoAgentConfig` 已定义：
  - `front_door.mode`
  - `front_door.bearer_token_env`
  - `front_door.trusted_proxy_*`
  - `channels.telegram.dm_policy`
  - `channels.telegram.allow_users`
  - `channels.telegram.allowed_groups`
  - `channels.telegram.group_policy`
  - `channels.telegram.group_allow_users`
- 但 `control_plane.py` 当前 ui hints 只暴露：
  - runtime
  - providers
  - model_aliases
  - telegram enabled/mode/token/webhook/allow_users/allowed_groups
- `SettingsCenter.tsx` 只是把 hints 分成 `main-agent / channels / advanced`，没有安全 review 语义

结论：

- 036 必须先补 canonical hints 和 review summary，再做 UX。
- 否则图形化设置只是“把遗漏字段继续遗漏”。

## 1.4 Agent Profile 和权限默认值仍然是静默生成

证据：

- `apps/gateway/src/octoagent/gateway/services/agent_context.py`
- `packages/policy/src/octoagent/policy/models.py`
- `control_plane.py::get_agent_profiles_document()`

现状：

- `AgentContextService._ensure_agent_profile()` 会自动生成 system/project profile，且默认：
  - `tool_profile="standard"`
  - `model_alias="main"`
- Policy profile 虽然有 `default / strict / permissive`，但当前只存在于代码模型里，没有统一的用户配置入口。
- control plane 仅有 `agent_profile.refresh`，没有 `agent_profile.save` 或 policy select action。

结论：

- 036 必须把“主 Agent 风格 / 权限等级 / approval 强度”做成显式可配置对象。
- 当前隐藏默认值会直接削弱可用性和安全可解释性。

## 1.5 Tools / Skills / MCP 已有 runtime truth，但没有 setup 级治理表达

证据：

- `capability_pack.py`
- `mcp_registry.py`
- `core/models/capability.py`
- `control_plane.py::get_capability_pack_document()`

现状：

- Capability pack 已能输出：
  - tools 列表
  - skills 列表
  - worker profiles
  - tool availability / install hint / runtime kind
- MCP registry 已能输出 server/tool 发现结果和错误状态
- 但当前没有 setup 视角回答：
  - 哪些 tools/skills 对当前主 Agent 默认可用
  - 哪些 skills 需要额外依赖或 secrets
  - 哪些 MCP servers 当前未配置或不可用
  - 用户应不应该开启它们

结论：

- 036 需要新增 setup-governance 级别的 capability projection，而不是逼前端自己拼 capability pack + mcp + policy。

## 1.6 035 已经提供图形化壳，但 setup/governance 还没冻结

证据：

- `.specify/features/035-guided-user-workbench/spec.md`
- `frontend/src/pages/SettingsCenter.tsx`
- `frontend/src/workbench/utils.ts`

现状：

- 035 已把首页、聊天、Work、Memory、Settings、Advanced 立起来
- 但设置页目前仍主要围绕 `ConfigSchemaDocument` 的简化字段渲染
- agent profile / policy / skills / safety review 没有正式产品契约
- 现有前端字段路径工具主要按对象路径处理，尚未真正支持 `providers.0.id` 这类 richer schema path
- workbench 资源路由映射当前也还没有把 `agent_profiles / owner_profile / bootstrap_session / context_continuity` 纳入统一的前端刷新适配层

结论：

- 036 应作为 035 的下一层 contract freeze 和治理增强
- 不能靠 035 Settings 页面继续“边做边猜”
- 036 实施时必须同步补前端 `resource mapping + field path resolver`，否则 richer setup contract 仍然无法真正接上现有 UI

## 2. 关键设计边界

### D1. 不新造平行 settings backend

正确做法：

- 新增的 setup/governance 能力必须继续挂在 `/api/control/resources/*` 和 `/api/control/actions`
- CLI 与 Web 同步消费同一套 document / action

### D2. Wizard 是 draft 容器，不是临时命令脚本

正确做法：

- 扩 `WizardSessionDocument` 或等价 canonical setup session
- 把 provider/channel/security/profile/tool/skill 的 draft 都挂到同一 durable session

### D3. 安全 review 必须是后端产物

正确做法：

- 后端产出 risk summary / warnings / blocking reasons
- 前端只做呈现，不自行判断“是否安全”

### D4. Tools / Skills / MCP 必须用 readiness 表达，而不是 registry 生数据

正确做法：

- 向 setup 提供“ready / missing secret / missing binary / disabled / degraded”语义
- install hint、reason、scope 必须是 canonical 字段

## 3. 技术风险

1. 如果继续让 onboarding 输出命令而不是 draft/apply，036 很难同时改善 Web 和 CLI。
2. 如果 Agent Profile / policy profile 仍然没有保存动作，设置页最终只能展示“事实快照”，不能真正治理。
3. 如果 skill readiness 仍停留在 capability pack 生列表，用户会继续碰到“显示支持，但并不真能用”的接缝。
4. 如果安全 review 在前端硬编码，Web、CLI、Telegram 的表达会再次漂移。
