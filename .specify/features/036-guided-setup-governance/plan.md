# Implementation Plan: Feature 036 Guided Setup Governance

**Branch**: `codex/feat-036-guided-setup-governance` | **Date**: 2026-03-10 | **Spec**: `.specify/features/036-guided-setup-governance/spec.md`  
**Input**: `.specify/features/036-guided-setup-governance/spec.md` + Feature 015 / 025 / 026 / 030 / 035 基线 + OpenClaw / Agent Zero 本地与在线参考

---

## Summary

036 不做“另一套设置系统”，而是在既有 wizard / control-plane / capability / secret / project 基线之上，把 `Provider / Channel / Agent Profile / 权限 / Tools / Skills` 的初始化配置与默认治理收口成真正的 canonical setup 主链。

核心目标有四个：

1. 提供统一的 `setup-governance` 投影，让 Web 和 CLI 共享同一 setup 事实源；
2. 提供统一的 `setup.review / setup.apply` 语义，让用户在 apply 前明确知道风险和缺口；
3. 让 Agent Profile、policy preset、tool level、skills readiness 成为正式可治理对象；
4. 把安全边界默认暴露在 setup 中，而不是继续藏在高级模式或静默默认值里。

---

## Technical Context

**Language / Version**:

- Python 3.12+
- TypeScript 5.8
- React 19.1

**Primary Dependencies**:

- `octoagent/packages/provider/src/octoagent/provider/dx/*`
- `octoagent/apps/gateway/src/octoagent/gateway/services/control_plane.py`
- `octoagent/apps/gateway/src/octoagent/gateway/services/agent_context.py`
- `octoagent/apps/gateway/src/octoagent/gateway/services/capability_pack.py`
- `octoagent/apps/gateway/src/octoagent/gateway/services/mcp_registry.py`
- `octoagent/packages/policy/src/octoagent/policy/*`
- `octoagent/frontend/src/pages/SettingsCenter.tsx`

**Target Platform**:

- 本地单 owner
- 可信内网 / 受控公网部署
- Web 与 CLI 双入口

**Testing Strategy**:

- `octoagent/apps/gateway/tests/test_control_plane_api.py`
- `octoagent/apps/gateway/tests/test_setup_governance*.py`
- `octoagent/packages/provider/tests/test_onboarding*.py`
- `octoagent/frontend/src/pages/SettingsCenter.test.tsx`
- setup e2e / CLI integration smoke

**Constraints**:

- 不新增平行 `setup/*` 私有 API
- 不直接写 YAML 或 secret store
- 不泄露 secret 实值
- 不把 036 做成仅 Web 可用的功能
- 不重做 033 上下文连续性领域逻辑

---

## Constitution Check

| Constitution 原则 | 适用性 | 评估 | 说明 |
|---|---|---|---|
| 原则 1: Durability First | 直接适用 | PASS | setup draft / review / apply 结果必须可追溯并挂到 durable store / event |
| 原则 2: Everything is an Event | 直接适用 | PASS | `setup.review` / `setup.apply` / `agent_profile.save` 必须进入 control-plane event 流 |
| 原则 3: Tools are Contracts | 直接适用 | PASS | tools/skills readiness 必须来源于 canonical capability / mcp / policy contract |
| 原则 5: Least Privilege by Default | 直接适用 | PASS | 036 的核心就是把最小权限和默认暴露面做成显式设置 |
| 原则 6: Degrade Gracefully | 直接适用 | PASS | capability/mcp/secret audit 任一降级时 setup 仍需分 section 工作 |
| 原则 7: User-in-Control | 直接适用 | PASS | 高风险 preset、channel exposure、skills enablement 必须显式 review/apply |
| 原则 8: Observability is a Feature | 直接适用 | PASS | 用户必须能看懂当前 Agent 实际权限和当前 setup 风险 |

**结论**: 036 可以直接进入设计与实现，但必须把“统一 canonical setup flow + secret redaction + Web/CLI 一致性”作为头号硬门禁。

---

## Project Structure

### 文档制品

```text
.specify/features/036-guided-setup-governance/
├── spec.md
├── plan.md
├── tasks.md
├── checklists/
│   └── requirements.md
├── contracts/
│   ├── setup-governance-contract.md
│   └── setup-review-apply-contract.md
├── research/
│   ├── product-research.md
│   ├── tech-research.md
│   ├── online-research.md
│   └── research-synthesis.md
└── verification/
    └── verification-report.md
```

### 预期代码与测试变更布局

```text
octoagent/packages/provider/src/octoagent/provider/dx/
├── cli.py
├── onboarding_service.py
├── wizard_session.py
├── config_schema.py
└── (必要时新增 setup governance models/service)

octoagent/apps/gateway/src/octoagent/gateway/services/
├── control_plane.py
├── agent_context.py
├── capability_pack.py
├── mcp_registry.py
└── (必要时新增 setup_governance.py)

octoagent/packages/policy/src/octoagent/policy/
└── models.py / runtime binding

octoagent/frontend/src/
├── pages/SettingsCenter.tsx
├── pages/Home.tsx
├── types/index.ts
├── workbench/utils.ts
└── components/setup/*

octoagent/apps/gateway/tests/
├── test_control_plane_api.py
├── test_setup_governance.py
└── e2e/test_setup_governance_e2e.py
```

---

## Architecture

### 1. Setup Governance Projection

新增 `SetupGovernanceDocument`，作为 setup 的一等 canonical projection。

它不是新 truth store，而是对以下事实源的聚合：

- `ConfigSchemaDocument`
- `WizardSessionDocument`
- `ProjectSelectorDocument`
- `AgentProfilesDocument`
- `OwnerProfileDocument`
- `CapabilityPackDocument`
- `SecretService.audit()`
- `Doctor/diagnostics`
- `McpRegistryService`
- `PolicyProfile` registry

目的：

- 让 Web 和 CLI 不再各自拼 setup 状态
- 让用户看到的是人话 section，而不是零散底层资源

### 2. Draft / Review / Apply Lifecycle

036 必须把 setup 从“命令链”升级为“draft -> review -> apply”链路。

推荐实现：

1. 扩 `WizardSessionRecord` 或新增等价 durable setup draft 容器
2. `setup.review` 基于 draft 生成统一风险摘要
3. `setup.apply` 统一协调：
   - `config.apply`
   - agent profile 保存
   - policy preset 绑定
   - skills selection 保存
4. apply 完成后刷新：
   - `config`
   - `setup_governance`
   - `agent_profiles`
   - `capability_pack`
   - `diagnostics`

### 3. Safe Preset Mapping

036 不应把小白用户直接暴露给 `allowed_tool_profile`、`reversible_action` 这些低层字段。

应建立一层正式 preset：

- `谨慎`
- `平衡`
- `自主`

它们必须映射到：

- `PolicyProfile`
- effective `tool_profile`
- approval 行为
- review 风险等级

### 4. Provider / Channel / Security Surface

036 必须让 setup 第一屏就能解释：

- 当前使用哪个 provider / main / cheap model
- secret bindings 是否完整
- `front_door.mode` 当前意味着谁可以访问
- Telegram 是 `pairing / allowlist / open / disabled`
- 群聊是否开放、是否限制成员

关键设计要求：

- schema 字段真实来源继续来自 `OctoAgentConfig`
- setup 中的风险说明来自后端 review，而不是前端硬编码判断

### 5. Agent / Tools / Skills Governance Surface

036 必须把以下对象做成 setup 的正式 section：

- active Agent profile
- owner overlay
- policy preset
- effective tool level
- skill readiness
- MCP server readiness

关键设计要求：

- tools/skills 不只展示“已注册”，还要展示：
  - availability
  - missing requirements
  - install hint
  - trust level
  - default enabled scope

### 6. Web / CLI Convergence

#### Web

- 035 `SettingsCenter` / `Home` 消费 `setup-governance`
- 提供 review/apply UI 和风险摘要 UI
- 同步补 `workbench/utils.ts` 的字段路径解析和 resource route mapping，确保 richer canonical documents 能被真正刷新和渲染

#### CLI

- `octo init` 成为 canonical setup adapter
- `octo onboard` 读取同一 setup projection 输出摘要

这样可以避免：

- Web 改一套、CLI 改一套
- 将来又出现“做了但是没有关联上”

### 7. Security & Redaction

036 的所有新增 document / action / event 都必须经过 redaction 设计：

- 只展示 env ref / secret target
- 不展示实际 secret value
- 不在 event summary 中写凭证内容
- 对 OAuth / webhook secret / bearer token 同样适用

---

## Interface Strategy

### 必须直接复用的接口

- `GET /api/control/snapshot`
- `GET /api/control/resources/wizard`
- `GET /api/control/resources/config`
- `GET /api/control/resources/project-selector`
- `GET /api/control/resources/agent-profiles`
- `GET /api/control/resources/owner-profile`
- `GET /api/control/resources/capability-pack`
- `GET /api/control/resources/diagnostics`
- `POST /api/control/actions`

### 建议新增的 canonical resources

- `GET /api/control/resources/setup-governance`
- `GET /api/control/resources/policy-profiles`
- `GET /api/control/resources/skill-governance`

### 建议新增的 canonical actions

- `setup.review`
- `setup.apply`
- `agent_profile.save`
- `policy_profile.select`
- `skills.selection.save`

### 明确禁止

- `/api/setup/*`
- 直接前端写 `octoagent.yaml`
- Web 单独保存 Agent/Profile/Skills 私有 JSON
- CLI 单独绕过 control-plane 直接改同一批设置

---

## Risks

### 1. Wizard 膨胀风险

如果把所有治理项塞进老 wizard 而不重新定义 section/readiness/review，CLI 会变得更长但不更清楚。

缓解：

- 以 `setup-governance` 为主投影
- wizard 作为 durable draft，不直接承担全部呈现逻辑

### 2. Policy 绑定落地不完整

如果只有 UI preset，没有运行时 policy select，用户会得到错误安全预期。

缓解：

- `policy_profile.select` 成为正式 action
- 增加 effective policy projection 测试

### 3. Skills readiness 伪实现

如果继续只展示 capability pack 列表，没有 requirements 检查，036 会重复 032 之前的“catalog 看起来很强”问题。

缓解：

- 单独定义 `skill-governance` 文档
- 接入 capability + mcp + secret audit 三方交叉判断

### 4. Web / CLI 再次漂移

如果 SettingsCenter 先行做私有逻辑，CLI 后续再补，仍然会出现两套心智模型。

缓解：

- 先冻结 contract
- 先补 backend documents/actions
- 再接 UI 与 CLI

---

## Recommended Delivery Order

1. 先补 canonical resources / actions / redaction
2. 再补 CLI `init/onboard` 共享 setup flow
3. 再补 035 Settings/Home 的 setup integration
4. 最后补完整 e2e 与 backlog 回写

---

## Conclusion

036 不是可选的美化项，而是 035 之后必须尽快补上的“设置与治理闭环”。

如果不做 036，OctoAgent 会继续处于这种状态：

- 工作台看起来更像产品了
- 但初始化配置仍然偏工程化
- 安全边界仍然偏隐式
- Agent / Tools / Skills 的默认权限仍然难以被普通用户理解

因此，036 应当作为 M4 的正式配置治理 Feature 进入实现。
