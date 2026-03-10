# Research Synthesis: Feature 036 — Guided Setup Governance

## 1. 综合判断

036 适合现在启动，而且必须作为 035 后续的 contract 强化 Feature 推进。

原因很明确：

- 035 已经把用户工作台壳子搭起来了；
- 但 Provider / Channel / Agent / 权限 / Tools / Skills 的初始化治理仍然是碎片化的；
- 如果不尽快冻结 036，035 Settings 和 onboarding 只会继续积累“界面更好看，但配置仍不安全、也不连贯”的债。

## 2. 已确认事实

### 2.1 当前系统已有足够的 canonical building blocks

已经存在：

- `ConfigSchemaDocument`
- `WizardSessionDocument`
- `ProjectSelectorDocument`
- `AgentProfilesDocument`
- `OwnerProfileDocument`
- `CapabilityPackDocument`
- `McpRegistryService`
- `SecretService.audit()`
- `PolicyProfile`

因此 036 不需要新造独立设置系统。

### 2.2 真缺口在“治理表达”和“共享 apply 链”

当前最大的结构性问题是：

1. 初始化步骤分散在 CLI / onboarding / secrets / settings / hidden defaults
2. 安全字段存在于 schema，但不在主路径中被解释
3. Agent Profile / policy / tool level / skill readiness 没有统一产品面
4. Web 和 CLI 没有共享的 review / apply 语义

## 3. 冻结后的 036 定义

036 的正确定位是：

> 在复用 Feature 015 / 025 / 026 / 030 / 035 既有 contract 的前提下，交付一个真正面向用户的 Setup Governance 主链，使 Provider / Channel / Agent Profile / 权限 / Tools / Skills 的配置、风险审查和应用过程成为同一套 canonical flow。

## 4. 四个必须同时成立的支柱

### A. Setup 必须有统一的 canonical projection

不能再让前端或 CLI 自己拼：

- config
- diagnostics
- agent_profiles
- capability pack
- secret audit

036 必须提供一个 setup-governance 级别的正式投影。

### B. Draft / Review / Apply 必须可追溯

如果用户配置 Provider、Channel、Agent 权限后没有统一 review，最终仍然会在 apply 时产生 surprise。

### C. 安全边界必须默认可见

front-door、pairing、allowlist、tool level、approval 强度、skills readiness 都必须成为 setup 首屏信息，而不是高级模式术语。

### D. Web 与 CLI 必须消费同一真相

如果 036 只服务 Web，不服务 `octo init` / `octo onboard`，CLI 会继续漂移；
如果 036 只服务 CLI，035 的设置中心也会变成半成品。

## 5. 非伪实现门禁

036 必须显式启用以下硬门禁：

1. 用户看到的 setup 状态必须能追溯到 control-plane canonical resources。
2. 用户执行的保存、review、apply 必须走 action registry，而不是直接写文件。
3. Agent Profile / policy / tool level / skills readiness 必须有正式 document/action，不允许只读展示。
4. Secrets 仍必须保持 refs-only；任何 review/event/document 都不得泄露明文。
5. 验收必须证明 Web 和 CLI 至少各有一条完整 setup 主路径。

## 6. 最终建议

- 036 应作为 M4 的正式 Feature 冻结并进入实施准备。
- 036 应直接依赖 035 的 UI 壳和 015/025/026/030 的既有 contract。
- 036 的成功标准是：普通用户可以完成初始化，并明确知道自己的 Agent、Channel、Tools、Skills 实际会做什么、不会做什么，以及为什么。
