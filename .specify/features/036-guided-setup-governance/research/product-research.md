# Product Research: Feature 036 — Guided Setup Governance

## 结论

036 的核心不是“再做更多设置项”，而是把 `Provider / Channel / Agent Profile / 权限 / Tools / Skills` 的初始化配置收口成一条普通用户真能走通、也真能看懂风险的主路径。

当前 OctoAgent 最大的问题不是能力缺失，而是**设置入口碎片化 + 安全边界表达缺席 + 默认值静默生效**。这对小白用户和安全敏感用户都不友好。

## 对标观察

## 1. OpenClaw：把安全和初始化当成产品的一部分

参考：

- `_references/opensource/openclaw/README.md`
- `_references/opensource/openclaw/docs/start/wizard.md`
- `_references/opensource/openclaw/docs/channels/pairing.md`
- `_references/opensource/openclaw/docs/concepts/oauth.md`
- `_references/opensource/openclaw/docs/tools/skills.md`
- `_references/opensource/openclaw/src/pairing/pairing-store.ts`
- `_references/opensource/openclaw/src/agents/auth-profiles/store.ts`
- `_references/opensource/openclaw/src/agents/skills-status.ts`

产品信号：

- `openclaw onboard` 不是只收集配置，而是把 gateway、workspace、channels、skills 串成一条连续引导。
- Pairing、device trust、DM access 都是显式流程，不靠用户自己猜默认暴露面。
- Auth profile 是一等产品对象，不是埋在 env 里的实现细节。
- Skills 不只展示“已安装”，还展示 eligibility、缺失依赖和 install options。

对 036 的启发：

- 初始化流程必须同时回答“能不能用”和“会暴露什么风险”。
- Channel 接入不能只问 token 和 mode，还必须明确 pairing / allowlist / group access。
- Tools / Skills 不能只有 registry，必须有“可用、不可用、为什么、要不要启用”的产品表达。

## 2. Agent Zero：把设置、项目边界和技能导入做成长期使用入口

参考：

- `_references/opensource/agent-zero/README.md`
- `_references/opensource/agent-zero/docs/guides/projects.md`
- `_references/opensource/agent-zero/docs/guides/usage.md`
- `_references/opensource/agent-zero/conf/model_providers.yaml`
- `_references/opensource/agent-zero/webui/components/settings/agent/chat_model.html`
- `_references/opensource/agent-zero/webui/components/settings/agent/agent.html`
- `_references/opensource/agent-zero/webui/components/settings/skills/import.html`
- `_references/opensource/agent-zero/webui/components/settings/external/auth.html`

产品信号：

- Provider/model/API key/base URL 这些高频设置，在 Web UI 里就是第一等入口。
- Projects 是长期边界：instructions、memory、secrets、git、skills 都围绕 project 管理。
- Skills import 有 preview、scope、namespace、conflict policy，而不是“装了就算完成”。
- 用户可以显式选择默认 agent profile，而不是接受后台静默生成的 profile。

对 036 的启发：

- Provider、Agent Profile、Skills 不能只在 CLI 或底层 store 存在，必须有图形化治理入口。
- Skills 的启用/导入必须带 preview 和风险说明，否则用户只会得到“可配但不可控”的体验。
- “主 Agent 是什么风格、权限多大、默认会不会自己动手” 必须从实现细节升级为用户理解层。

## 3. 当前 OctoAgent 的用户感知缺口

### 3.1 首次配置仍然是工程脑回路

用户现在需要在下面这些入口之间切换：

- `octo init`
- `octo onboard`
- `octo config *`
- `octo secrets *`
- Web `SettingsCenter`
- control-plane `config` / `wizard` / `capability` / `agent_profiles`

用户感知到的是“命令越来越多”，而不是“设置越来越清楚”。

### 3.2 安全边界没有被产品化

真实系统里已经存在：

- `front_door.mode`
- `channels.telegram.dm_policy`
- `channels.telegram.group_policy`
- `allow_users / allowed_groups / group_allow_users`
- policy profile / tool profile

但普通用户在当前 Web/CLI 主路径中几乎看不到这些项的含义、默认值和风险。

### 3.3 Agent / 权限 / Tools / Skills 还不是一条线

当前用户很难回答下面这些基本问题：

1. 主 Agent 默认是谨慎还是自主？
2. 它允许用到什么级别的工具？
3. 哪些 Skills 真的能跑，哪些只是“看起来注册了”？
4. MCP server 配错了会怎样？

这意味着系统对普通用户来说并不“可治理”。

## 产品目标收敛

### 目标一：初始化配置必须是一条连续的“设置 + 风险审查”路径

用户应该能在一次 flow 里完成：

- 选择 Provider / model
- 配置 front-door / channel
- 选择主 Agent 风格与权限 preset
- 确认 Tools / Skills / MCP readiness
- 看 review summary 后再 apply

### 目标二：默认值必须显式，不允许静默接管用户决策

尤其是：

- Agent Profile 默认创建
- tool profile 默认级别
- Channel 暴露面
- Pairing / allowlist 策略

都必须在 setup review 里清楚展示。

### 目标三：Web 和 CLI 必须说同一种话

不允许出现：

- Web 用“Main Agent / Safety Level”
- CLI 用 `tool_profile / allowed_tool_profile / profile_filter`

却没有统一映射的情况。

### 目标四：安全不是高级模式专属

安全边界和默认权限必须在新手路径中就可见，而不是被藏进 Advanced。

## 产品决策

- 036 作为 035 之后的配置治理深化 Feature 是合理的，而且应尽快推进。
- 036 不应新造独立 setup backend，而应扩既有 wizard / control-plane / capability / agent profile contract。
- 036 的成功标准不是“多了几个设置面板”，而是“普通用户能安全地完成初始化并理解当前 Agent 的实际权限边界”。
