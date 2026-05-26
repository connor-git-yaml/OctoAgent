# F103b Blueprint Limitations 收尾 — Spec

> Feature ID: F103b
> Slug: blueprint-limitations
> Type: **纯文档 Feature**（严禁任何 .py / .ts / .tsx 改动）
> Stage: M5 → M6 过渡阶段（与 F103c 并行）
> Baseline: origin/master @ `def6638`（F103 完成）
> Worktree: `.claude/worktrees/funny-cray-549fb6`（复用既有 worktree，原分支 `claude/funny-cray-549fb6` 已改名为 `feature/103b-blueprint-limitations`）
> Branch: `feature/103b-blueprint-limitations`
> 上游依据: CLAUDE.md / CLAUDE.local.md §"M5 → M6 过渡阶段（2026-05-25 拍板）" / F103 handoff.md §6 "已知 limitations"

---

## 0. 目标与背景

### 0.1 目标

把 F103 范围明确排除的 3 个 Blueprint 文档同步到 master 现状，关闭 F103 文档化的 3 项已知 limitation，让 M5 真正干净收口：

1. **core-design.md（913 行）** 同步 F084 Harness Layer + F101 NotificationService + F102 DailyRoutineService 三大新组件设计细节
2. **deployment-and-ops.md（564 行）** 同步 F081 ProviderRouter 替代 LiteLLM Proxy 的部署影响
3. **testing-strategy.md（162 行）** 同步 F083 测试并发优化 + F087 E2E Live Test Suite + F089 MCP E2E Testing 测试基础设施变更

### 0.2 背景

- **F103 状态**：M5 13 Feature 收口，但 F103 incremental scope 主动排除 3 个 large 子文档，明确归档为"M6 期间独立 Feature 顺手清"——CLAUDE.local.md F103 实施记录 §"已知 limitations"。
- **M5 → M6 过渡阶段决策（2026-05-25）**：用户拍板新增 F103b（本 Feature）+ F103c（Worker Log/Error 表面规范化），两者完成后 M5 真正干净收口才启 M6 F104。
- **F103c 并行硬约束**：F103c 同时在另一个 worktree（`feature/103c-worker-log-error`）改动 `worker_runtime.py` / `task_runner.py` / logger 配置。F103b 严禁动任何 .py 文件，避免冲突。
- **数据源 SoT**：
  - **F081 ProviderRouter**：`.specify/features/081-litellm-full-retirement/` + `docs/codebase-architecture/provider-direct-routing.md`
  - **F083 测试并发优化**：CLAUDE.md "Feature 083 实施记录" + `docs/codebase-architecture/testing-concurrency.md`
  - **F084 Harness 全栈**：`.specify/features/084-context-and-harness/` + `docs/codebase-architecture/harness-and-context.md` + CLAUDE.md "Feature 084 实施记录"
  - **F087 e2e_live**：CLAUDE.md "Feature 087 实施记录" + `docs/codebase-architecture/e2e-testing.md`
  - **F089 MCP E2E**：`.specify/features/089-mcp-e2e-testing/`（若存在）+ pytest 集成
  - **F101 NotificationService**：CLAUDE.local.md F101 实施记录 + `.specify/features/101-notification-attention/`
  - **F102 DailyRoutineService**：CLAUDE.local.md F102 实施记录 + `.specify/features/102-proactive-followup/`

### 0.3 完成定义

- 3 个 Blueprint 子文档同步完成（每文件 ≥ 1 个 commit）
- 7 个 Feature 改动主体（F081/F083/F084/F087/F089/F101/F102）进入 Blueprint 对应子文档
- F103 baseline (def6638) 全量回归 0 regression
- e2e_smoke PASS
- 每 Phase Codex review 闭环（0 high 残留）；F103 主 session 接管 fallback 模式可用
- Final cross-Phase Codex review 通过
- completion-report.md 含 3 文件 diff 统计 + 修订条目对照表
- 与 F103c 合并验证（Final 阶段 rebase 后跑全量回归）

---

## 1. 不在范围（明确排除）

> 禁令优先于指令，每条附带原因（CLAUDE.md §"Prompt 与规则编写"）。

| 禁令 | 原因 |
|------|------|
| **不动任何 .py / .ts / .tsx 文件** | F103b 是纯文档 Feature；任何代码改动违反 scope，且与 F103c 冲突 |
| **不动 worker_runtime.py / task_runner.py / logger 配置** | 这些是 F103c 主战场；F103b 不得修改任何运行时行为 |
| **不动 docs/blueprint/blueprint.md 顶级索引** | F103 已更新到 M5-Delivered；F103b 仅扩展子文档内部章节 |
| **不动 docs/blueprint/agent-collaboration-philosophy.md** | F103 新建文档，本 Feature 不动 |
| **不动 docs/codebase-architecture/message-model.md** | F103 新建文档，本 Feature 不动 |
| **不重写整个章节** | 仅 incremental 增补新章节（§8.5.7 / §8.10 / §12.1.4 / §13.11 等），保留既有内容 |
| **不动 .specify/features/081-103/** 已有 spec | immutable 历史记录 |
| **不动 CLAUDE.md / CLAUDE.local.md** | 已是同步源 SoT，反向流违反单一事实源 |
| **不实施 F107 推迟项** | F107 主范围，F103b 仅文档化"推迟"事实（如有必要） |
| **不做 Blueprint v0.2 重组** | v0.1 incremental 修订 |
| **不动测试代码** | 纯文档应无测试影响，跑一次回归确认即可 |

---

## 2. Acceptance Criteria（AC）

> 12 AC：每 Block（A/B/C）2-3 AC + 全局 4 AC。AC 通过 = 实际产出对照本 spec 验证。

### AC-A core-design.md 同步（F084/F101/F102）

- **AC-A1 §8.5.7 Harness Layer 新增**：core-design.md §8.5 Tooling 章节末尾新增 §8.5.7 "Harness Layer（F084 引入）"，含 ToolRegistry / ToolsetResolver / ThreatScanner / SnapshotStore / ApprovalGate / DelegationManager 6 子组件设计细节
- **AC-A2 §8.6.6 ApprovalGate SSE 接入**：§8.6 Policy Engine 末尾新增 §8.6.6 "ApprovalGate（F084 + F101 WAITING_APPROVAL）"，含 session allowlist / SSE 通道 / WAITING_APPROVAL 状态机（task_runner 单 owner + CAS + 双注册）/ ApprovalManager 桥接 / startup recovery
- **AC-A3 §8.7.6 Context Layer + USER.md SoT**：§8.7 Memory 末尾新增 §8.7.6 "Context Layer（F084 USER.md SoT）"，含 USER.md → OwnerProfile 派生只读视图 / user_profile.update/read/observe 三工具 / Memory Candidates API（promote/discard/bulk_discard with atomic claim + skipped_ids）/ WriteResult 通用回显契约
- **AC-A4 §8.10 NotificationService + DailyRoutineService 新增**：core-design.md 末尾新增 §8.10 "Notification & Routine（F101 + F102）"：
  - §8.10.1 NotificationService：4 级优先级（critical/high/medium/low）+ quiet hours USER.md SoT + Web/Telegram dismiss 同步 + sha256 notification_id + NOTIFICATION_DISPATCHED EventType
  - §8.10.2 DailyRoutineService：cron 触发 + 9 步执行 + LLM/fallback + token budget 截断（max_input ≤ 2000 字符 / max_output ≤ 512 token）+ USER.md 3 字段解析（daily_summary_time / routine_active / summary_channels）+ ROUTINE_TRIGGERED/COMPLETED/FAILED/SKIPPED 4 EventType

### AC-B deployment-and-ops.md 同步（F081）

- **AC-B1 §12.1.4 ProviderRouter 直连**：§12.1 部署拓扑末尾新增 §12.1.4 "ProviderRouter 直连（F081 替代 LiteLLM Proxy）"，含：
  - 退役 LiteLLM Proxy 子进程（不再启动 4000 端口的 litellm proxy 容器/进程）
  - 3 种 transport（OpenAI Chat / OpenAI Responses / Anthropic Messages）直连
  - alias 解析（语义 alias → provider alias → endpoint）
  - 凭证管理（`~/.octoagent/auth-profiles.json` 替代 LiteLLM 主 master_key）
  - 部署拓扑简化（生产 Compose 删除 litellm 服务）
- **AC-B2 §12.9.1 `octo config` 段补充 ProviderRouter**：§12.9.1 末尾增补 ProviderRouter alias 解析 / auth-profiles.json 注入路径 / 与历史 LiteLLM 配置的迁移说明（不重复 F081 migrate-080 命令细节，引用 .specify/features/081 即可）
- **AC-B3 §8.9 Provider Plane 重写**：core-design.md §8.9 整段重写（原标题"§8.9 Provider Plane：LiteLLM alias 策略"→"§8.9 Provider Plane：ProviderRouter 直连"），§8.9.1-8.9.4 子节内容同步替换为 ProviderRouter 模型（语义 alias 保留 / 运行时 alias 废除 LiteLLM / 统一成本治理改为 ProviderRouter 内部记账 / 多 Provider 扩展改为 transport 适配器）

  > **注意**：AC-B3 属于 core-design.md 的修订，归在 Block B（deployment-and-ops.md 同主题），实施在 Phase A 内完成（避免 core-design.md 拆两次 commit）。

### AC-C testing-strategy.md 同步（F083/F087/F089）

- **AC-C1 §13.1 测试基础设施增补**：§13.1 末尾新增子节 §13.1.1 "测试并发优化（F083）"，含 thread shutdown hang 修（aiosqlite + asyncio executor）/ fixture os.environ 污染修 / attach_input 测试 race 加严等待 / xdist opt-in（`pytest -n auto`，task_runner 状态机测试 ~20% 失败率作为已知工程债）
- **AC-C2 §13.11 E2E Live Test Suite 新增**：testing-strategy.md 末尾新增 §13.11 "E2E Live Test Suite（F087）"，含：
  - OctoHarness 抽离（`gateway/harness/octo_harness.py` 4 个 DI 钩子：credential_store / secret_store / transport_factory / clock）
  - 13 能力域（smoke 5 + full 8）清单 + GATE_P3_DEVIATION（full 中 4 域直调主路径绕开 LLM 不确定性）
  - Hermetic 隔离（双 autouse fixture 重置 5 类凭证 env / 4 个 OCTOAGENT_* 路径 env / 5 项 module 单例）
  - `octo e2e` CLI 4 模式（smoke / full / `<id>` / `--list` / `--loop=N`）
  - pre-commit hook（`make install-hooks` worktree-aware）+ 180s portable watchdog + `SKIP_E2E=1` 紧急 bypass
  - SC-7 不变量（USER.md / auth-profiles.json / mcp-servers/ sha256 完全一致）
- **AC-C3 §13.12 MCP E2E Testing 新增**（如 F089 已落地）：末尾新增 §13.12 "MCP E2E Testing（F089）"，含 supervisor 模式 / delete_config 治本 / leak detection / pyt psutil（若 F089 未在 baseline def6638 内完整落地，AC-C3 调整为 ≤ 30 行简略概述 + 标注"详见 .specify/features/089"）

### AC-D 全局验收

- **AC-D1 引用路径精确**：所有 incremental 新增的子节，引用代码路径时精确到文件（packages/core/src/octoagent/... 或 apps/...），新组件可引用类名/函数名但不强制行号（baseline def6638 上代码可能后续演化）
- **AC-D2 链接不破坏**：3 文件原有 markdown 链接（如 `[xxx](xxx.md)`）保持有效
- **AC-D3 中文输出**：所有新增/修订段落使用中文（CLAUDE.md §"语言与风格"）
- **AC-D4 回归 + e2e_smoke**：F103 baseline (def6638) 全量回归 0 regression；e2e_smoke PASS；Final 阶段 rebase F103c 完成后的 master 再跑一次

---

## 3. Functional Requirements（FR）

> FR 是 AC 的实施细节。

### FR-A core-design.md 同步（对应 AC-A1~A4 + AC-B3）

- **FR-A1 §8.5.7 Harness Layer 新增**（≥ 80 行）：
  - **ToolRegistry**：数据驱动 entrypoints（`packages/core/src/octoagent/harness/tool_registry.py`），注册期 fail-fast（WriteResult 子类强制）
  - **ToolsetResolver**：按 Worker / Subagent kind 解析可用工具集；与 capability_pack 协同
  - **ThreatScanner**：17+ pattern + invisible Unicode 检测；命中即 block；与 ApprovalGate 配合
  - **SnapshotStore**：冻结快照 + Live State 二分（保护 prefix cache）；F084 关键设计
  - **ApprovalGate**：session allowlist + SSE；F099 escalate_permission 接入入口
  - **DelegationManager**：max_depth=2 / max_concurrent=3 限制；F092 收敛后唯一编排入口
  - 引用 `docs/codebase-architecture/harness-and-context.md` 作为详细实现文档

- **FR-A2 §8.6.6 ApprovalGate（F101 WAITING_APPROVAL 状态机）**（≥ 40 行）：
  - WAITING_APPROVAL 状态机改造（task_runner 单 owner + CAS + 双注册桥接 ApprovalManager）
  - SSE 通道（前端实时收审批请求）
  - F099 escalate_permission 三工具走 ApprovalGate production 接入（F101 真闭环）
  - startup recovery（重启后未决审批从 EventStore 恢复）

- **FR-A3 §8.7.6 Context Layer + USER.md SoT**（≥ 60 行）：
  - **USER.md 是 SoT**：`~/.octoagent/USER.md` 直接用户可读可编辑；OwnerProfile 退化为派生只读视图
  - **user_profile 三工具**：update（写 USER.md）/ read（读派生 OwnerProfile）/ observe（候选 fact 提取）
  - **Memory Candidates API**：promote（候选 → permanent）/ discard / bulk_discard with atomic claim + skipped_ids；Web UI 红点 badge
  - **WriteResult 通用回显契约**：18+ 写工具 return type 强制 WriteResult 子类；保留 task_id / memory_id / run_id 等关联键不压扁
  - **退役**：BootstrapSession / BootstrapOrchestrator / UserMdRenderer / bootstrap_integrity / bootstrap_commands CLI（净删 ~2400 行 dead code）
  - 引用 `docs/codebase-architecture/harness-and-context.md`

- **FR-A4 §8.10 Notification & Routine 新增**（≥ 100 行）：
  - **§8.10.1 NotificationService**：
    - 4 级优先级（critical/high/medium/low）+ 各级 channel routing（critical → Telegram + Web 强提示 / low → 仅 Web silent）
    - quiet hours：USER.md SoT 解析（22:00-07:00 等用户偏好），discard 而非 enqueue
    - dismiss 跨通道统一：Telegram callback + Web API；dismiss 持久化 推迟到 F107（**当前重启清空 LOW**）
    - sha256 notification_id（去重 + dedupe）
    - NOTIFICATION_DISPATCHED EventType（每条 notification 写 event_store，含 quiet hours 内被过滤的）
  - **§8.10.2 DailyRoutineService**：
    - cron 触发（daily 默认）+ 9 步执行流程
    - LLM 总结主路径 + deterministic fallback（LLM 不可用时降级）
    - token budget 截断（max_input ≤ 2000 字符 / max_output ≤ 512 token）
    - USER.md 3 字段解析（daily_summary_time / routine_active / summary_channels）
    - 4 新 EventType（ROUTINE_TRIGGERED / ROUTINE_COMPLETED / ROUTINE_FAILED / ROUTINE_SKIPPED）挂在 `_daily_routine_audit` task
    - SD-10 时区语义（UTC 归一化）+ OCTOAGENT_USER_TIMEZONE env 兜底

- **FR-A5 §8.9 Provider Plane 整段重写**（≥ 50 行，AC-B3）：
  - 标题 "§8.9 Provider Plane：LiteLLM alias 策略" → "§8.9 Provider Plane：ProviderRouter 直连"
  - §8.9.1 语义 alias（业务侧）保留
  - §8.9.2 运行时 alias 段重写：废除 LiteLLM Proxy，直连 provider HTTP（3 种 transport）
  - §8.9.3 统一成本治理段重写：ProviderRouter 内部记账，不再依赖 LiteLLM 成本模块
  - §8.9.4 多 Provider 扩展重写：transport 适配器模式（OpenAI Chat / OpenAI Responses / Anthropic Messages）
  - 引用 `docs/codebase-architecture/provider-direct-routing.md`

### FR-B deployment-and-ops.md 同步（对应 AC-B1~B2）

- **FR-B1 §12.1.4 ProviderRouter 直连**（≥ 40 行）：
  - 退役 LiteLLM Proxy 子进程：开发拓扑（§12.1.1）+ 生产拓扑（§12.1.2）均不再启动 litellm proxy
  - 3 种 transport 配置示例（auth-profiles.json 片段）
  - alias 解析路径（语义 alias → provider alias → endpoint）
  - 凭证管理（`~/.octoagent/auth-profiles.json` 替代 LiteLLM master_key）
  - 部署拓扑简化（docker-compose.yml 中删除 litellm 服务的具体行号或片段）
  - migrate-080 提示（详见 F081）

- **FR-B2 §12.9.1 octo config 段补充**：
  - 仅追加 ProviderRouter 相关说明（auth-profiles.json + transport_factory 注入路径）
  - 历史 LiteLLM 配置的迁移说明（引用 F081 migrate-080 命令，不复制内容）

### FR-C testing-strategy.md 同步（对应 AC-C1~C3）

- **FR-C1 §13.1.1 测试并发优化**（≥ 30 行）：
  - 进程退出从 30+ 分钟 hang → ~20s（关键修复）
  - aiosqlite + asyncio executor 修 thread shutdown hang
  - fixture os.environ 污染修
  - attach_input 测试 race 加严等待
  - xdist opt-in：`pytest -n auto` 5.5x 提速但 task_runner 状态机测试 ~20% 失败率（已知工程债，治本超 F083 scope）
  - 引用 `docs/codebase-architecture/testing-concurrency.md`

- **FR-C2 §13.11 E2E Live Test Suite**（≥ 80 行）：
  - **OctoHarness 抽离**：`gateway/harness/octo_harness.py` 4 个 DI 钩子（credential_store / secret_store / transport_factory / clock）+ 内置 120s ProviderRouter timeout + 30s SIGALRM 单测 watchdog
  - **13 能力域**：
    - smoke 5：#1 工具调用基础 / #2 USER.md 全链路 / #3 冻结快照 / #11 ThreatScanner block / #12 ApprovalGate SSE
    - full 8：Memory promote / Perplexity MCP / Skill / Graph Pipeline / delegate_task / max_depth / A2A / Routine cron
    - smoke = 集成层 + DI stub；full 中 4 域直调主路径绕开 LLM 不确定性（GATE_P3_DEVIATION）
  - **Hermetic 隔离**：双 autouse fixture 重置 5 类凭证 env + 4 个 OCTOAGENT_* 路径 env + 5 项 module 单例（清单见 `MODULE_SINGLETONS.md`）
  - **pre-commit hook**：`make install-hooks`（worktree-aware）→ commit 自动跑 `pytest -m e2e_smoke` 180s portable watchdog（python3 SIGTERM→SIGKILL，不依赖 macOS gtimeout）+ `SKIP_E2E=1` 紧急 bypass
  - **`octo e2e` CLI**：4 模式（smoke / full / `<id>` / `--list` / `--loop=N`）
  - **不变量**：≥ 3026 passed / 0 regression；smoke 5x 循环 4s/iter；SC-7 跑前后 USER.md / auth-profiles.json / mcp-servers/ sha256 完全一致
  - 引用 `docs/codebase-architecture/e2e-testing.md`

- **FR-C3 §13.12 MCP E2E Testing**（≤ 30 行 或 ≥ 50 行，视 F089 落地度而定）：
  - 若 F089 已完整落地 baseline def6638：详细写 supervisor 模式 / delete_config 治本 / leak detection / pyt psutil
  - 若 F089 部分落地或未启动：≤ 30 行简略概述 + 标注"详见 .specify/features/089"或"M5 阶段 3 启动"
  - **实测先行**：Phase C 实施前 grep 验证 F089 在 baseline def6638 的实际状态，AC-C3 范围按实测调整

---

## 4. 跨 Block 不变量

- **I-1 行为零变更**：F103b 是纯文档 Feature，所有运行时行为 100% 等价于 baseline `def6638`
- **I-2 测试零回归**：F103 baseline 测试数保持（≥ 3649 passed，按 F103 commit 实测）
- **I-3 不破坏现有链接**：所有 docs/blueprint/ + docs/codebase-architecture/ 内 `[xxx](xxx.md)` 链接保持有效
- **I-4 中文输出**：所有新增/修订段落使用中文（CLAUDE.md §"语言与风格"）
- **I-5 SoT 单一性**：Blueprint 与 CLAUDE.local.md / docs/codebase-architecture/ 内容重叠时，Blueprint 是面向"长期协作者"的产品文档，CLAUDE.local.md 是面向"当前实施者"的工作记录——重叠的实施记录在 Blueprint 内只保留索引指针 + 关键摘要
- **I-6 与 F103c 不交叉**：F103b 不动 worker_runtime.py / task_runner.py / logger 配置 / 任何 .py 文件

---

## 5. Phase 划分（详细见 plan.md）

| Phase | 名称 | 产出 | 工作量 |
|-------|------|------|------|
| **A** | core-design.md 同步（最大）| §8.5.7 + §8.6.6 + §8.7.6 + §8.9 重写 + §8.10（F084/F101/F102/F081）| 大（≥ 300 行新增）|
| **B** | deployment-and-ops.md 同步 | §12.1.4 + §12.9.1 增补（F081）| 中（≥ 50 行新增）|
| **C** | testing-strategy.md 同步 | §13.1.1 + §13.11 + §13.12（F083/F087/F089）| 中（≥ 110 行新增）|
| **Final** | Codex review + completion-report + handoff + 回归 + rebase F103c | codex-review-final.md / completion-report.md / handoff.md + 回归 0 regression | 必走 |

---

## 6. 关键决策点（GATE_DESIGN）

### 6.1 Phase 顺序（已默认 A→B→C→Final）

按 spec.md §5 顺序执行。理由：A 最大工作量先做，建立信心 + 早期暴露内容准确性问题（A 含 §8.9 重写涉及 F081/B 同主题，统一在 A 做避免 core-design.md 拆两次 commit）。

### 6.2 §8.9 Provider Plane 重写归 Phase A 还是 Phase B（已默认 A）

- **A（推荐）归 Phase A**：core-design.md 改动一次成型，避免单文件多次 commit；Phase B 仅动 deployment-and-ops.md
- B 归 Phase B：deployment-and-ops.md + §8.9 同 commit，但 core-design.md 会被拆成 Phase A + Phase B 两次 commit（合理性低）

### 6.3 §13.12 MCP E2E Testing 详略（视实测调整）

- 若 F089 已完整落地 baseline def6638：FR-C3 详细写
- 若 F089 部分落地或未启动：FR-C3 简略概述 + 标注引用
- **Phase C 实施前先 grep 验证**，AC-C3 范围按实测调整

### 6.4 §8.10 Notification + Routine 合并 vs 拆分（已默认合并）

- **合并（推荐）**：§8.10 一节含 §8.10.1 NotificationService + §8.10.2 DailyRoutineService，主题相关（都是用户感知 ROI 层）
- 拆分：§8.10 NotificationService + §8.11 DailyRoutineService，独立编号——但 core-design.md 已有 §8.9，§8.10 + §8.11 顺序合理但占两个顶级编号

### 6.5 §8.5.7 Harness Layer 详略

每个子组件（ToolRegistry / ToolsetResolver / ThreatScanner / SnapshotStore / ApprovalGate / DelegationManager）≥ 10 行设计描述，整段 ≥ 80 行；详细实现引用 `docs/codebase-architecture/harness-and-context.md`，避免在 Blueprint 内复制实现细节。

---

## 7. 全局回归（每 Phase 后 + Final）

> 纯文档 Feature 理论无测试影响，但仍跑回归确认。

- **每 Phase 后**：`pytest -m e2e_smoke`（≤ 5s 期望）
- **Final 前**：完整回归（`pytest packages/ apps/ -m "not slow and not e2e_live"`，≥ 3649 passed 期望 vs F103 baseline）
- **Final 中**：rebase F103c 完成后的 master，再跑一次完整回归确认无冲突
- F103 baseline 行：3649 passed（vs F102 baseline +78）—— F103b 应保持 ≥ 3649

---

## 8. Final cross-Phase Codex Review 要点

> CLAUDE.local.md §"工作流改进"强制：F103b commit 前必走 Final cross-Phase review。
> F103 fallback 模式可用：若 Codex 网络中断，主 session 按以下重点接管 review。

review 重点（纯文档 review 与代码 review 不同）：

1. **内容准确性 vs 代码现状**：每段新增/重写章节，对照 baseline def6638 的实际代码验证：
   - §8.5.7 ToolRegistry → 验证 `packages/core/src/octoagent/harness/tool_registry.py` 真实存在
   - §8.10.1 NotificationService → 验证 `NOTIFICATION_DISPATCHED` EventType 在 baseline 真实定义
   - §8.10.2 DailyRoutineService → 验证 4 个 ROUTINE_* EventType + `_daily_routine_audit` task 真实存在
   - §12.1.4 ProviderRouter → 验证 docker-compose.yml 在 baseline 是否真删了 litellm 服务（若未删，文档需注明"建议在 F104 部署阶段同步"）
   - §13.11 e2e_live → 验证 `gateway/harness/octo_harness.py` + 13 能力域真实存在
2. **引用路径精确**：所有引用代码路径精确到文件，可不带行号但文件必须存在；引用 `docs/codebase-architecture/*` 子文档必须存在
3. **链接完整性**：新增/修订段落不破坏既有 markdown 链接
4. **SoT 不重复**：与 CLAUDE.local.md / docs/codebase-architecture/ 重叠的实施细节，Blueprint 内只保留摘要 + 引用，不复制
5. **与 F103c 不冲突**：rebase F103c 完成的 master 后回归 0 regression
6. **中文输出**：所有新增段落中文

---

## 9. 验收 checklist（完成时必须回报）

### Block A 验收（core-design.md）

- [ ] AC-A1 §8.5.7 Harness Layer 新增 ≥ 80 行（6 子组件齐全）
- [ ] AC-A2 §8.6.6 ApprovalGate（F084 + F101 WAITING_APPROVAL）≥ 40 行
- [ ] AC-A3 §8.7.6 Context Layer + USER.md SoT ≥ 60 行（三工具 + Memory Candidates + WriteResult + 退役清单）
- [ ] AC-A4 §8.10 Notification + Routine ≥ 100 行（NotificationService + DailyRoutineService 完整）
- [ ] AC-B3 §8.9 Provider Plane 整段重写 ≥ 50 行（标题改 + 4 子节同步）

### Block B 验收（deployment-and-ops.md）

- [ ] AC-B1 §12.1.4 ProviderRouter 直连 ≥ 40 行
- [ ] AC-B2 §12.9.1 octo config 补充 ProviderRouter

### Block C 验收（testing-strategy.md）

- [ ] AC-C1 §13.1.1 测试并发优化（F083）≥ 30 行
- [ ] AC-C2 §13.11 E2E Live Test Suite（F087）≥ 80 行
- [ ] AC-C3 §13.12 MCP E2E Testing（F089，详略视实测）

### 全局验收

- [ ] AC-D1 引用路径精确到文件
- [ ] AC-D2 markdown 链接不破坏
- [ ] AC-D3 中文输出
- [ ] AC-D4 回归 + e2e_smoke：F103 baseline (def6638) 全量 0 regression + e2e_smoke PASS + rebase F103c 后再跑全量
- [ ] Final Codex review 闭环（0 high 残留）
- [ ] completion-report.md 含 3 文件 diff 统计 + 7 Feature 修订条目对照表
- [ ] handoff.md 给 M6 第 1 个 Feature（F104）决策建议
- [ ] 不主动 push origin/master，归总报告等用户拍板

---

## 10. 与 F103c 协调

- F103b（本 Feature）：纯文档，3 个 Blueprint 子文档
- F103c（另一个 worktree）：代码 Feature，worker_runtime.py / task_runner.py / logger 配置
- **不交叉文件**：F103b 不动 .py；F103c 不动 docs/blueprint/
- **Final 阶段必须 rebase**：F103c 先完成 push origin/master 时，F103b Final 阶段 rebase 后跑全量回归确认
- **若 F103b 先完成**：通知用户后等 F103c 完成；按 CLAUDE.local.md §"Spawned Task 处理流程"不主动 push
