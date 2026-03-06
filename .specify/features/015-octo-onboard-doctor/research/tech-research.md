# Feature 015 技术调研：Octo Onboard + Doctor Guided Remediation

**特性分支**: `codex/feat-015-octo-onboard-doctor`
**调研日期**: 2026-03-07
**调研模式**: full（含在线调研）
**产品调研基础**: `research/product-research.md`

## 1. 调研问题

1. 当前代码距离 `octo onboard` 还缺哪些基础结构？
2. `octo onboard` 应该建立在哪个现有入口之上，才能避免重复实现？
3. 如何在不吞掉 Feature 016 职责的前提下，保留 channel step？
4. doctor 怎样从“检查项列表”升级到“可恢复的 remediation 流程”？

## 2. 当前代码基线（AgentsStudy）

### 2.1 CLI 已有统一入口，但还没有 onboard 命令

- `octoagent/packages/provider/src/octoagent/provider/dx/cli.py`
  - 当前仅注册 `config`、`init`、`doctor`。
- `octoagent/packages/provider/pyproject.toml`
  - `octo = "octoagent.provider.dx.cli:main"` 已经存在，CLI 暴露路径稳定。

结论：015 不需要新建 CLI 包，只需在现有 `dx.cli` 注册 `onboard` 子命令。

### 2.2 `octo config` 已是当前基线，`octo init` 仍是历史路径

- `config_commands.py`
  - 已具备 `provider add/list/disable`、`alias list/set`、`sync`、`migrate` 等能力。
- `init_wizard.py`
  - 仍按旧流程直接处理 `.env` / `.env.litellm` / `litellm-config.yaml`。

结论：015 的 provider 配置阶段应直接复用 Feature 014 的 `octo config` 体系，而不是再扩展 `init_wizard.py`。`octo init` 只保留兼容入口，不应成为 onboarding 主路径。

### 2.3 `DoctorRunner` 有检查项，但没有“阶段化 remediation”

- `doctor.py`
  - 当前产出是 `DoctorReport(checks=[CheckResult...])`。
  - `CheckResult` 只有 `name/status/level/message/fix_hint`。
  - `format_report()` 输出为平铺表格，没有阶段归组和下一步动作模型。

结论：015 至少需要补充两层抽象：

1. 结构化 remediation action（如 command / config / manual / blocked）；
2. onboarding 可消费的 stage summary（provider/runtime/channel/readiness）。

### 2.4 当前没有任何 onboarding 持久化

- `dx/` 目录下没有 `onboarding_session`、`onboarding_store`、`resume` 相关模型或存储。
- 现有 `InitConfig` 仅表示一次初始化结果，不包含可恢复流程语义。

结论：015 需要新增一套最小模型：

- `OnboardingSession`
- `OnboardingStepState`
- `OnboardingRemediation`
- `OnboardingSummary`

### 2.5 渠道步骤当前无本地实现，必须用 contract 对接

- 仓库当前尚未出现稳定的 Telegram channel 代码路径；M2 规划中 Telegram 属于 Feature 016。
- `docs/m2-feature-split.md` 明确：015 是 onboarding 闭环；016 是 Telegram channel + pairing + routing。

结论：015 必须设计 `ChannelOnboardingVerifier` 之类的 contract，允许：

- verifier 已注册 -> 执行 channel readiness / first-message verification；
- verifier 未注册 -> 输出 blocked 状态和下一步依赖说明。

这样 015/016 才能真正并行。

## 3. 参考实现证据

### 3.1 OpenClaw：onboard 作为推荐入口，doctor 作为修复工具

- `/_references/opensource/openclaw/README.md`
  - 推荐用户首先运行 `openclaw onboard`。
- `/_references/opensource/openclaw/src/commands/onboard.ts`
  - onboarding 根据 runtime 和模式切换 interactive / non-interactive 路径。
- `/_references/opensource/openclaw/docs/gateway/doctor.md`
  - doctor 除健康检查外，提供 repair / deep / non-interactive 等修复路径。

启示：015 的 `octo onboard` 应当是推荐主路径，而不是只做一个薄壳 wrapper。

### 3.2 OpenClaw：pairing 和 dashboard 都是 onboarding 周边，而不是替代品

- `/_references/opensource/openclaw/docs/channels/pairing.md`
  - pairing 是 owner approval step，默认前置。
- `/_references/opensource/openclaw/src/commands/dashboard.ts`
  - dashboard 负责打开控制面和 token 解析，不代替 onboarding。
- `/_references/opensource/openclaw/docs/gateway/troubleshooting.md`
  - 通过命令阶梯定位 no replies / dashboard connectivity / channel policy。

启示：015 需要明确“控制面补充入口”和“首次配置主路径”的分工，不让用户在 dashboard 和 onboard 之间迷路。

### 3.3 Agent Zero：先达成 first working chat，再谈高级能力

- `/_references/opensource/agent-zero/knowledge/main/about/installation.md`
  - 安装目标直接定义为“go from zero to a first working chat”。
- `/_references/opensource/agent-zero/README.md`
  - 强调实时可干预、可保存/加载聊天、日志自动落盘。

启示：015 的完成标准应聚焦 first usable state，而不是只验证配置文件存在。

## 4. 方案对比

### 方案 A：在 `octo init` 上继续堆功能

- 优点：改动路径短
- 缺点：继续绑定旧三文件路径；和 Feature 014 的 `octo config` 基线冲突；恢复语义难补

### 方案 B：新增 `octo onboard` 编排层，复用 `config` 和 `doctor`（推荐）

- 优点：与 blueprint 一致；能显式承载 session/checkpoint；可以通过 verifier contract 与 016 并行
- 缺点：需要新模型和状态持久化

### 方案 C：只增强 `octo doctor`，不做向导

- 优点：实现最省
- 缺点：无法解决路径连续性，也无法解决中断恢复

## 5. 技术决策建议

1. **主入口**：新增 `octo onboard`，直接挂到现有 `dx.cli`。
2. **配置基线**：provider/runtime 阶段只调用 `octo config` 体系，不再以 `init_wizard.py` 为主路径。
3. **状态持久化**：引入项目级 onboarding session 持久化文件，记录当前步骤、阻塞项、最近 remediation、最后更新时间。
4. **doctor 升级方式**：保留 `DoctorReport` 和表格输出兼容，同时新增结构化 remediation summary 供 `octo onboard` 消费。
5. **channel 解耦**：定义 channel verifier registry / protocol；015 只编排，不内嵌 Telegram transport 逻辑。
6. **终态定义**：至少区分 `BLOCKED`、`ACTION_REQUIRED`、`READY` 三种 onboarding 总状态。

## 6. 风险与缓解

- 风险：015 直接调用 Click 命令导致流程难测试
  - 缓解：把 onboarding 编排逻辑放到独立 service/module，CLI 只做参数解析和展示。
- 风险：session 状态文件与项目根定位不一致
  - 缓解：沿用 `config_commands.py` 的 `_resolve_project_root()` 语义，保证 `--yaml-path`/环境变量/`cwd` 一致。
- 风险：doctor 扩展破坏现有测试
  - 缓解：对现有 `DoctorReport` 输出保持兼容，新增字段走增量模型扩展。
- 风险：channel verifier 缺位导致流程卡死
  - 缓解：verifier 缺位时输出明确 blocked remediation，而不是异常退出。

## 7. 在线补充结论（摘要）

详见 `research/online-research.md`。

- OpenClaw 官方路径明确把 onboard 作为推荐入口，并把 doctor、pairing、dashboard 作为连续操作面的一部分。
- Agent Zero 从用户价值上强调 first working chat、intervene、save/load/backup，说明 onboarding 需要构建“可回来”的信心，而不是只跑完配置。

## 8. 结论

Feature 015 的最佳技术路径是“新增可恢复的 onboarding 编排层”，而不是继续改造 `octo init`。

最小可行方案：

- `octo onboard` 命令
- `OnboardingSession` 持久化
- `DoctorRemediation` 结构化动作
- `ChannelOnboardingVerifier` contract
- 针对中断恢复与 blocked state 的 E2E 测试
