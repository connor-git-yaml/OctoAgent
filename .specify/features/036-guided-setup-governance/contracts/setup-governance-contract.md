# Contract: Setup Governance Canonical Interface

## 1. 目标

`Feature 036` 不新增平行 settings backend。所有初始化配置与治理能力必须继续挂在 control-plane canonical 体系下，并由 Web / CLI 共用。

## 2. Canonical Read Surfaces

### 2.1 保留并复用的既有资源

- `GET /api/control/resources/wizard`
- `GET /api/control/resources/config`
- `GET /api/control/resources/project-selector`
- `GET /api/control/resources/agent-profiles`
- `GET /api/control/resources/owner-profile`
- `GET /api/control/resources/capability-pack`
- `GET /api/control/resources/diagnostics`

### 2.2 036 新增 canonical 资源

#### `GET /api/control/resources/setup-governance`

单一 setup 投影，面向 Web `Settings / Setup Center` 与 CLI `octo init` / `octo onboard`。

必须聚合以下信息：

- `project_scope`
  - 当前 project / workspace
  - 继承来源与 fallback reason
- `provider_runtime`
  - enabled providers
  - alias completeness
  - secret audit summary
  - litellm sync status
- `channel_access`
  - enabled channels
  - mode
  - pairing / allowlist / group policy / exposure summary
  - channel readiness
- `agent_governance`
  - active agent profile
  - owner overlay
  - policy preset
  - effective tool_profile / approval level
- `tools_skills`
  - capability pack summary
  - MCP server health
  - skill readiness items
  - install hints / missing requirements
- `review`
  - warnings
  - blocking reasons
  - risk level
  - next actions

约束：

- `setup-governance` 只是 canonical aggregation，不得变成新的 truth store。
- 其中每个 section 都必须携带 `source_refs`，指向底层 canonical documents 或 store-derived projections。

#### `GET /api/control/resources/policy-profiles`

输出当前可选 policy presets，例如：

- `strict`
- `default`
- `permissive`

每个 profile 至少包含：

- `profile_id`
- `label`
- `description`
- `allowed_tool_profile`
- `approval_policy`
- `risk_level`
- `recommended_for`

#### `GET /api/control/resources/skill-governance`

输出 setup 视角下的 skill / MCP readiness：

- `scope`
- `enabled_by_default`
- `availability`
- `missing_requirements`
- `required_secrets`
- `install_hint`
- `trust_level`
- `source_kind`（builtin / workspace / mcp）

## 3. Canonical Mutation Surfaces

所有变更继续走 `POST /api/control/actions`。

### 3.1 保留动作

- `config.apply`
- `wizard.refresh`
- `wizard.restart`
- `project.select`
- `capability.refresh`

### 3.2 036 新增动作

#### `setup.review`

输入：

- `draft.config`
- `draft.agent_profile`
- `draft.policy_profile_id`
- `draft.skill_selection`
- `draft.channel_selection`

输出：

- 风险审查摘要
- blocking reasons
- warnings
- 需要额外 secrets / install / doctor 的项
- `resource_refs = [setup-governance]`

约束：

- `setup.review` 只做校验与汇总，不落盘
- 不得返回 secret 明文

#### `setup.apply`

输入与 `setup.review` 同结构。

行为：

- 统一协调 `config.apply`
- agent/profile/policy 的落盘
- 触发 capability/diagnostics 刷新
- 返回所有受影响的 `resource_refs`

约束：

- apply 必须具备幂等性
- 任一子步骤失败时，必须返回分段结果与明确补救动作

#### `agent_profile.save`

用于保存 project/system 作用域下的主 Agent profile。

至少支持：

- `profile_id`
- `scope`
- `project_id`
- `name`
- `persona_summary`
- `instruction_overlays`
- `model_alias`
- `tool_profile`

#### `policy_profile.select`

用于把用户选择的安全 preset 映射到实际 policy profile。

#### `skills.selection.save`

用于保存用户对 built-in / workspace / MCP skills 的默认启用范围与禁用列表。

## 4. Snapshot Integration

`GET /api/control/snapshot` 在 036 完成后必须至少新增：

- `setup_governance`
- `policy_profiles`
- `skill_governance`

这样 035 的首页与设置中心可以直接消费 setup 状态，而不是额外绕资源拉齐。

## 5. CLI Integration

### `octo init`

必须改为消费 `setup-governance` + `setup.review` + `setup.apply` 的 CLI adapter。

### `octo onboard`

必须从“输出命令建议”升级为：

- 输出当前 setup 状态
- 输出风险摘要
- 明确阻塞项
- 必要时触发 review / apply / doctor 建议

## 6. 禁止项

- 禁止新增 `/api/setup/*` 私有接口
- 禁止前端或 CLI 直接写 `octoagent.yaml`
- 禁止将 secret 实值写入 setup document、action result 或 control-plane events
- 禁止前端自行推断“技能是否安全”，必须以后端 readiness 为准
