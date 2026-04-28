# Research Synthesis — Feature 084

> 综合 product-research.md + tech-research.md 的关键结论，供后续 specify / plan / tasks 阶段引用。
> 遇到产品诉求与技术选型有冲突时本文件给出仲裁。

## 1. 一致结论（直接进 spec）

### 1.1 核心问题定性
- F082 治标未根治。问题不是单一 bug，而是 **D1 Tool Registry entrypoints 缺失 web 入口** + **D2 Snapshot Store 不存在** + **D3 is_filled 误判** + **D4 工具命名语义失配** 四个架构断层。
- **Hermes Agent 是首要参考**（snapshot 模式 + tool registry + approval + routine + delegation）。其他 4 个 reference 提供局部启发但不是主基准。

### 1.2 6 个模块技术选型（已锁定）

| 模块 | 推荐方案 | 关键理由 |
|---|---|---|
| **SnapshotStore** | 内存 dict 冻结 + atomic rename + fcntl.flock | Hermes 生产验证 / prefix cache 保护 / 0 新依赖 |
| **Tool Registry** | AST 扫描 + module-level `register()` | Hermes 可移植约 450 行 / 自描述工具 / 热更新支持 |
| **Threat Scanner** | 正则 pattern table + invisible unicode 检测 | 微秒级 / 离线可用 / 与 PolicyGate 解耦 |
| **Routine Scheduler** | `asyncio.Task` 轻量调度（不用 APScheduler） | 与现有 cron 完全隔离 / cancel/pause 语义精确 |
| **Sub-agent Delegation** | 分层：读用 ProviderRouter 直调 / 写走 ToolBroker+PolicyGate | C4 Two-Phase 合规 / 避免 Worker spawn 开销 |
| **USER.md 合并** | append-only `§` 分隔符（Hermes 模式）+ add/replace/remove 三操作 | 0 token 成本 / 用户审视成本低 / 无需 markdown parser |

### 1.3 不引入的新依赖（已确认）
- ❌ filelock / portalocker（用 `fcntl.flock` 标准库）
- ❌ markdown-it-py / mistune（append-only 模式不需要 parser）
- ❌ APScheduler 扩展用法（用 `asyncio.Task`）
- ❌ 新 LLM SDK（用现有 ProviderRouter）

### 1.4 用户旅程优先级（与用户决策一致）

| 旅程 | 优先级 | 备注 |
|---|---|---|
| J1 USER.md 初始化（路径 A） | Must | 首场景，必须打通 |
| J2 LLM 写入回显 | Must | tool response 回显 → LLM 看到 → 用户看到 |
| J3 Threat Scan 拦截恶意输入 | Must | C5 / 防护必备 |
| J4 修改档案（覆盖 + section） | Must | append + replace + remove |
| J5 重装路径 | Must | 决策 6：清 data/ + behavior/ 后 octo update 重启 |
| J6 Observation 异步累积（路径 B） | Nice | 决策 2：首版上 |
| J7 UI promote 候选 → USER.md | Nice | 决策 4：首版做完整（候选列表 + 编辑 + a/r） |
| J8 sub-agent delegation | Nice | 决策 3：首版上（max_depth=2 + 黑名单） |
| J9 Approval 危险动作 | Must | 与 J3 一起进 ApprovalGate |

## 2. 冲突仲裁

### 仲裁 1：tech-research 推荐"先做迁移脚本（优先级最高）" vs 用户决策 6"不做 migrate 命令"

**裁决**：**用户决策为准 — 不做独立 migrate CLI 命令**。

但 tech-research 提到的 R7（OwnerProfile vs USER.md 双写一致性）是真问题。澄清：
- OwnerProfile 表退化为派生只读视图（决策 1），由 USER.md 解析 sync 而来
- 这个 sync 逻辑放在**实现内部**（启动时 + USER.md 写入 hook），**不**是 CLI 命令
- 用户重装路径仍是：清 `~/.octoagent/data/` + `~/.octoagent/behavior/` 后 `octo update`（保留 `octoagent.yaml` + `.env`）

### 仲裁 2：tech-research 建议"observation 首版限制结构化字段" vs 决策 2"首版上完整 observation"

**裁决**：**首版上完整，但加置信度 gate**。
- LLM 提取 confidence ≥ 0.7 的事实 → 进 candidates
- < 0.7 → 直接丢弃（不入 review queue 避免噪声）
- 这与决策 2"首版上"不冲突，是质量护栏

### 仲裁 3：tech-research 指出"UI promote 依赖 observation 质量，质量差时大量错误候选" vs 决策 4"首版做完整 UI promote"

**裁决**：**首版做完整 UI，加批量 reject + 静默归档机制**。
- 候选堆积超 30 天自动归档（不 promote）
- UI 提供"批量全选 reject"快速清理
- 用户体感差时可手动关闭 routine（feature flag）

## 3. 风险优先级矩阵（合并 9 个风险）

| 优先级 | 风险 | 概率 | 影响 | 缓解 | 落实在 |
|---|---|---|---|---|---|
| **P0** | R3 删除 ~2500 行 F082 代码遗漏依赖 → silent break | 中 | 高 | Phase 1 先 shim 保留接口；分阶段切；每 phase 独立回归 | plan + tasks Phase 1 / 4 |
| **P0** | R7 OwnerProfile vs USER.md 双写一致性 | 中 | 高 | USER.md 是 SoT；OwnerProfile 启动时从 USER.md 解析 sync；写入 USER.md 后触发 sync hook | plan + tasks Phase 2 |
| **P1** | R1 Snapshot mtime race（外部进程改 USER.md） | 中 | 中 | 写前 stat 比对 mtime；变化则 reload + merge；per-file `asyncio.Lock` | plan Phase 2 |
| **P1** | R2 Threat Scanner 误杀（false positive） | 中 | 中 | severity 分级（warn vs block）；提供 `--force` flag；Telegram approval 可旁路 | plan Phase 1 + 3 |
| **P1** | observation 质量 / 幻觉 | 中 | 中 | confidence ≥ 0.7 gate；用户每月 review；30 天自动归档 | plan Phase 3 |
| **P2** | R4 routine + 现有 cron 冲突 | 低 | 中 | 用 asyncio.Task 隔离；不复用 APScheduler 调度位 | plan Phase 3 |
| **P2** | R5 sub-agent token / latency 翻倍 | 中 | 低 | max_concurrent_children=3；max_depth=2；默认 LLM 主动调（不自动派发） | plan Phase 3 |
| **P2** | R6 UI promote 无人 review → 候选堆积 | 低 | 低 | 30 天自动归档 + UI 红点提醒 | plan Phase 3 |
| **P2** | R8 Tool Registry AST 扫描首次启动延迟 | 低 | 低 | 启动时 scan 一次缓存；测算 < 200ms 可接受 | plan Phase 1 |

## 4. Constitution 对齐补充（C2 必须满足）

新事件类型必须在 spec.md 明确定义并写入 SQLite event store schema：
- `MEMORY_ENTRY_ADDED`（user_profile.update / memory.add 落库后）
- `MEMORY_ENTRY_REPLACED`（覆盖式更新）
- `MEMORY_ENTRY_BLOCKED`（Threat Scanner 拦截）
- `OBSERVATION_OBSERVED`（user_profile.observe 调用后写入 candidates/）
- `OBSERVATION_STAGE_COMPLETED`（routine pipeline 每个 stage 完成）
- `OBSERVATION_PROMOTED`（用户 accept 后 candidate → USER.md）
- `OBSERVATION_DISCARDED`（用户 reject / 30 天自动归档）
- `SUBAGENT_SPAWNED` / `SUBAGENT_RETURNED`
- `APPROVAL_REQUESTED` / `APPROVAL_DECIDED`（已有 approval 机制扩展）

## 5. 进入 specify 阶段的输入清单

下一阶段（Phase 2 specify）应基于本 synthesis 生成 spec.md，覆盖：

1. **9 个用户旅程**（J1-J9）作为 User Stories
2. **6 模块技术选型**（已锁）作为 FR / 实现约束
3. **9 个风险**（P0-P2）作为 Risk 段落
4. **9 个新事件类型**作为 NFR / 数据合约
5. **3 个仲裁决策**（migration / observation gate / UI 批量 reject）作为 Scope Lock 补充
6. **5 阶段交付计划**（已在用户提案中固定）

---

*Synthesis 生成于 2026-04-28，作为 spec.md 的直接上游输入。冲突仲裁结论以用户已敲定的 6 个决策为最高优先级。*
