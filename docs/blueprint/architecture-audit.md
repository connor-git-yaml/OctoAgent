# 架构审计记录（2026-04）

> 本文件是 [blueprint.md](../blueprint.md) §14.5-14.8 的完整内容。

---

## 14.5 已知短板与改善方向（2026-04 审计）

> 基于 Claude Code / OpenClaw / Agent Zero 源码横向对比（见 `docs/design/`），
> 以下是当前主线代码最急需改善的五个短板，按严重程度排序。

### ✅ 短板 1：Context 管理缺乏自适应压缩（已修复 2026-04-04）

**现状问题**：
- `_SESSION_TRANSCRIPT_LIMIT = 20` 硬编码（`agent_context.py:128`），长对话关键历史被丢弃
- `ContextCompactionConfig` 包含 14 个参数但 `_fit_prompt_budget()` 未充分利用
- 压缩触发条件迟缓（`min_turns_to_compact = 4`）
- 无 Microcompact（增量合并短消息）机制

**对标差距**：
- Claude Code 用简单公式 `contextWindow - 13K` 做阈值，三级渐进（Micro → Auto → Memory）
- Agent Zero 用三级压缩（Topic 65% → Bulk 合并 → 丢弃），目标比 0.8
- OctoAgent 理论有三层压缩，但实际 Rolling Summary 质量和触发时机均不及前两者

**改善方向**：
- 去除硬编码 20 轮限制，改为基于 Token 的动态窗口
- 实现 Microcompact（合并相邻短消息降低消息数）
- 借鉴 Claude Code 的会话内存提取（长期记忆沉淀到 Memory）
- 简化压缩配置：用单一阈值公式替代 14 参数

### ✅ 短板 2：工具执行无并发能力（已修复 2026-04-04，Feature 070）

**现状问题**：
- `process_task_with_llm()` 和 `ToolBroker.execute()` 均为单工具顺序执行
- 无 `StreamingToolExecutor`，多工具场景存在 N 倍延迟
- 单工具无独立超时，只有 Worker 级 7200s 全局超时（`worker_runtime.py:80`）
- 工具执行无流式返回能力

**对标差距**：
- Claude Code 的 `StreamingToolExecutor` 支持并发执行 + 流式进度回调
- Agent Zero 的代码执行工具有独立的 `first_output_timeout`(30s) / `between_output_timeout`(15s) / `max_exec_timeout`(180s) 三级超时

**改善方向**：
- 在 SkillRunner 循环中添加 `asyncio.gather()` 并发工具调用
- 为每个工具增加独立超时配置
- 实现工具执行的流式中间结果推送

### ✅ 短板 3：权限模型缺少自动分类器（已修复 2026-04-04，Feature 070）

> 以下为修复前状态记录：

**现状问题**：
- 仅二层决策：PolicyGate（HIGH_RISK）→ ToolProfile（minimal/standard/privileged）
- 无命令级危险性自动分析（如 `ls` vs `rm -rf`）
- 所有非预设工具都需要明确授权或审批，UX 摩擦高

**对标差距**：
- Claude Code 四层决策（alwaysAllow → alwaysDeny → autoClassify → ask）+ `bashClassifier` 自动分析
- OpenClaw Tool Profile + 行为指导（TOOLS.md）双层

**改善方向**：
- 实现 `CommandSafetyClassifier`（分析 terminal.exec 命令危险性）
- 高置信度安全命令自动允许，降低用户打扰频率
- 补全 glob/regex 模式匹配规则

### ✅ 短板 4：Behavior 缓存无热更新（已修复 2026-04-04）

**现状问题**：
- `_behavior_pack_cache`（`agent_decision.py:45`）进程级全局字典，无 TTL
- 编辑 behavior 文件后必须手动 `invalidate_behavior_pack_cache()` 或重启进程
- `_BEHAVIOR_SIZE_WARNING_THRESHOLD = 15000` 仅告警无强制限制

**对标差距**：
- Claude Code 每次直接读文件，无缓存陈旧问题
- Agent Zero 通过 Extension 系统动态加载

**改善方向**：
- 为 BehaviorPack 缓存增加文件 mtime 校验（脏检查）
- 或设置 TTL 过期（如 60s），平衡性能和实时性
- behavior.write_file 工具写入后自动 invalidate

### ✅ 短板 5：Memory 系统过度工程化（已修复 2026-04-05）

**已修复内容**：
- `fast_commit()` 快速写入路径：低风险场景跳过 validate 查询，减少 1/3 DB 写入（confidence >= 0.75 + ADD + 非敏感分区）
- `memory_recall_completed` 结构化日志：latency_ms / candidate→delivered 漏斗 / scope_hit_distribution / backend_used
- 多 scope 查询改为 `asyncio.gather()` 并发，单 scope 失败不影响其他
- MemoryService God Class（2260 行 25 方法）拆分为 4 子服务 + Facade（680 行）
- MemoryConsoleService（1685 行）拆分为 5 模块
- models/integration.py（340 行 19 模型）拆分为 7 域文件
- 死代码清理：4 个死方法 + VaultAccessDecision 枚举简化为 bool
- SqliteMemoryStore：通用 _row_to_model + _build_filtered_query helpers 消除样板

## 14.6 重构中发现的架构问题（2026-04-04 实测审计）

> 基于 workspace 移除、Butler 统一、Policy 对齐、LiteLLM 集成等大规模重构中的实际问题排查，
> 以下是当前代码库中已确认影响用户体验的架构缺陷。

### ✅ 问题 1：Bootstrap 每个新对话都重走（已修复）

**已修复**：`agent_context.py` 中新 project 创建 `BootstrapSession` 时会检查全局 `OnboardingState`，已完成过则直接标记 `COMPLETED`；已有 session 如果全局 onboarding 完成但 session 仍 PENDING，自动升级。此外 behavior 加载路径（`project_root` 传递链 + `render_behavior_system_block` 的 `build_behavior_layers` 修复）也已在 2026-04-04 修复。

### ✅ 问题 2：`control_plane.py` 过于庞大（11,707 行）（已修复 2026-04-04）

**已修复**：拆分为 `control_plane/` 包（12 个独立模块）：
- `_coordinator.py`（~600 行 thin facade，字典路由替代 266 行 if/elif）
- `_base.py`（ControlPlaneContext + DomainServiceBase）
- 9 个 domain service：session / worker / memory / agent / mcp / setup / automation / work / import
- `_legacy.py` 已删除（11,707 行旧代码完全移除）

### ✅ 问题 3：LLM 双路径 History 格式不统一（已修复 2026-04-04，统一为 Chat Completions 格式）

**已修复**：`litellm_client.py` 内部 history 统一为 Chat Completions 标准格式（`tool_calls`/`tool` role）。Responses API 发送前由 `_history_to_responses_input()` 转换。assistant tool_calls 追加和 tool result 回填各从 3 条分支合并为 1 条。

### ✅ 问题 4：源码目录在 Agent 可访问范围内（已修复 2026-04-04）

**现状**：`~/.octoagent/app/octoagent/` 是系统源码但在 `instance_root` 下，LLM 的 `filesystem.read_text` 和 `terminal.exec` 能读到源码。

**影响**：LLM 反复读源码理解内部实现（如 `mcp_registry.py`），而不是直接使用 `mcp.install` 工具。浪费大量 token 和时间。

**已实现**：通过 PathAccessPolicy（path_policy.py）实现白名单/黑名单/灰名单三级强制拦截。Agent 的工作目录限定在 projects/{project}/，app/ 等系统目录被黑名单拒绝。

### ✅ 问题 5：工具数量过多（56 个）未按场景裁剪（已修复 2026-04-04，Feature 072）

**现状**：所有 56 个工具都注入给所有 Agent，包括 MCP 管理工具、系统诊断工具等。

**影响**：小模型（Qwen 35B）在 56 个工具面前 function calling 不稳定，有时只输出文本"让我搜索一下"但不实际调用。

**改善方向**：按 Agent profile、task 类型或对话阶段动态裁剪工具集。参考 Claude Code 的 Core + Deferred 分层。

### ✅ 问题 6：Memory Recall 与主对话共享模型别名（已修复）

**已修复**：Settings 页面已支持独立配置 `记忆加工`（cheap）、`查询扩写`（cheap）、`语义检索`（engine-default）、`结果重排`（rerank），与主对话 `main` 别名完全分离。SessionMemoryExtractor 使用 `resolve_default_model_alias()` 读取配置。

### ✅ 问题 7：数据模型残留大量 deprecated 字段（已修复 2026-04-05，Feature 073）

**现状**：`workspace_id`、`active_workspace_id`、`workspace_slug` 等字段虽标记 DEPRECATED 但仍占位。

**影响**：增加认知负担，JSON 序列化多余字段，新开发者困惑。

**改善方向**：下一个版本周期执行正式的数据迁移，删除 `workspaces` 表和所有 deprecated 字段。

**已修复**：Feature 073 已删除 ToolProfile 枚举和 Workspace 概念从模型/Store/Gateway 全层。Memory 包的 vault 审计表 workspace_id 列为可选且调用方全部传 None，待后续版本清理。

## 14.7 Worker 控制 + Subagent + Graph Pipeline 架构审计（2026-04-05）

> 基于 Agent Zero / OpenClaw 源码横向对比，以下是 Butler-Worker-Subagent 体系的架构评估。

### 架构优势（保持）

- **A2A 持久化通信**：Butler↔Worker 通过 A2AConversation + A2AMessageRecord 持久化，进程重启可恢复（Agent Zero 纯内存递归、OpenClaw session 推送都不具备）
- **Work 一等实体**：独立状态机（CREATED→ASSIGNED→RUNNING→终态），可查询/取消/合并（其他两个系统没有此抽象）
- **Worker 不可级联委派**：`_enforce_child_target_kind_policy()` 硬限制（Agent Zero 无此保护）
- **GraphPipelineTool**：结构化 DAG 编排能力（Agent Zero 和 OpenClaw 都不具备）

### 🟠 待改善项

#### W1: Worker 相关工具集过于膨胀（9 个 vs Agent Zero 的 1 个 / OpenClaw 的 2 个）

**现状**：9 个工具 — `subagents.spawn/list/kill/steer` + `workers.review` + `work.split/merge/delete/inspect`。其中 `work.split` 与 `subagents.spawn` 高度重叠（split 就是批量 spawn）。

**影响**：LLM 需要区分 9 个语义相近的工具，选择困难。Agent Zero 只用一个 `call_subordinate` 就完成了全部委派需求。

**改善方向**：考虑合并 `subagents.spawn` 和 `work.split`（spawn 支持批量 objectives 参数即可消除 split）。`workers.review` 改名为更明确的 `work.plan` 以区分规划和管理语义。

#### W2: DockerRuntimeBackend 是空壳

**现状**：继承 InlineRuntimeBackend 未重写任何方法，Docker 隔离实际未实现。

**改善方向**：M2 阶段实现或直接删除空壳类（Constitution #9 禁止保留无实际行为的代码）。

#### W3: GraphRuntimeBackend cancel_signal 未连接

**现状**：`cancel_signal=None` 传入 GraphRuntimeDeps，Graph backend 不可取消。

**改善方向**：连接 WorkerCancellationRegistry 的 cancel_event 到 Graph backend。

#### ✅ W4: Work 状态机形式化约束（已修复 2026-04-05）

**已修复**：
1. `core/models/delegation.py` 新增 `VALID_WORK_TRANSITIONS`（13 状态完整转换表）+ `WORK_TERMINAL_STATUSES` + `validate_work_transition()`
2. `delegation_plane.py` 的 `_transition_work()` / `complete_work()` / `mark_dispatched()` / `escalate_work()` 全部加入校验，非法转换 raise ValueError
3. `completed_at` 修复：从硬编码 `{CANCELLED, MERGED, DELETED}` 改为 `WORK_TERMINAL_STATUSES`
4. 散落的 `_WORK_TERMINAL_STATUSES` 收归 core 统一维护

#### W5: WAITING_INPUT 时 deadline 无限重置

**现状**：Worker runtime 中 task 处于 WAITING_INPUT 时 deadline 完全重置，可能永远不超时。

**改善方向**：设置 WAITING_INPUT 的最大等待时间（如 30min），超过后自动超时。

### Subagent 设计评估（已退役 — F087 followup 死代码清理 2026-05-01）

> 历史评估：曾设计为独立 AsyncioTask + 独立 SkillRunner + A2A 通信 + `kill_subagent` 优雅清理。资源限制（max_steps=100, max_duration=1800s）比 Worker（200, 7200s）更保守。
>
> **现状**：in-process `SubagentExecutor` 路径已被 Feature 084+ 的 `task_runner` 路径替代，`subagent_lifecycle.py` 整文件作为孤悬死代码删除。当前生产派子任务路径见 `docs/codebase-architecture/e2e-testing.md` §2.1。

### Graph Pipeline 设计评估

设计超前但功能完备。3 级 PipelineRegistry + 6 个 action（list/start/status/resume/cancel/retry）+ DELEGATE_GRAPH 决策模式。当前无结构性问题，待更多 Pipeline 定义积累后评估实际使用效果。

## 14.8 代码架构全面审计（2026-04-05）

> 基于包依赖方向、职责清晰度、模型层坏味道、概念一致性的全量扫描。

### ✅ A1: capability_pack.py God Object（已修复 2026-04-05）

**已修复**：47 个工具 handler 迁移到 `builtin_tools/` 子包，按域拆分为独立模块（filesystem、browser、memory、web、terminal 等）。`CapabilityPackService` 从 5,112 行瘦身到 2,138 行（-58%），退化为编排层。

### ✅ A2: provider/dx → apps/gateway 反向依赖（已修复 2026-04-05）

**已修复**：`setup_governance_adapter.py` 的 gateway import 改为延迟导入（`_ensure_gateway_imports()`），provider/pyproject.toml 删除 `octoagent-gateway` 依赖。`pip install octoagent-provider` 不再拉入 FastAPI/uvicorn。

**后续改善**：`dx/` 整体可提升为独立包 `packages/management/` 或 `apps/cli/`，进一步解耦。

### ✅ A3: tooling ↔ policy 循环依赖（已修复 2026-04-05）

**已修复**：
1. `SideEffectLevel` 枚举从 `tooling/models.py` 下沉到 `core/models/enums.py`，tooling 保留 re-export 保证向后兼容
2. `permission.py` 中 `ApprovalRequest` 构造改为传 dict，由 `ApprovalManager.register()` 入口做 `dict→ApprovalRequest` 转换
3. policy 包内 import 路径全部改为从 `core.models.enums` 导入 SideEffectLevel

**效果**：tooling 包不再有任何指向 policy 的 import（含延迟导入），循环依赖彻底消除。

### 🟠 A4: provider/dx 定位模糊（72 文件 24K 行）

CLI 命令、配置向导、backup、chat import、memory console 等管理面逻辑混在 "provider" 包中，依赖 memory + tooling + gateway。

**改善方向**：将 `dx/` 提升为独立包 `packages/management/` 或 `apps/cli/`。

### ✅ A5: control_plane.py 90 个模型混在单文件（��修复 2026-04-05）

**已修复**：`control_plane.py`（1172 行 90 个类）拆分为 `control_plane/` 子包（8 个领域子模块）：
- `_base.py`：枚举 + 基础协议（14 个）
- `session.py`：会话投影 + Context + Bootstrap���15 个）
- `agent.py`：Agent/Worker Profile + Policy + Skill（14 个）
- `memory.py`：Memory Console + SoR 审计 + Vault（14 个）
- `retrieval.py`：向量检索 + 索引管理（10 个）
- `setup.py`：Setup/Config/Wizard + MCP + Diagnostics（18 个）
- `work.py`：Delegation/Pipeline 投影（4 个）
- `automation.py`：自动化调度 + Action 注册（11 个）

`__init__.py` re-export 全部 90 个符号，消费方零改动。

### ✅ A6: Butler 遗留概念未清理（已修复 2026-04-05）

**已修复**：
- Agent Profile 名字不再加 " Butler" 后缀（`startup_bootstrap.py`）
- `ensure_butler_runtime_and_session` → `ensure_main_runtime_and_session`
- DDL 默认值 `butler`/`butler_main` → `main`/`main_bootstrap`
- 启动时数据迁移：自动去除已有数据的 " Butler" 后缀
- `normalize_runtime_role()`/`normalize_session_kind()` 兼容旧数据
- 前端 DOM ID `butler-fresh-turn` → `agent-fresh-turn`
- 枚举别名 `BUTLER`/`BUTLER_MAIN`/`BUTLER_PRIVATE` 标记 DEPRECATED（保留供旧数据反序列化）

### ✅ A7: 状态枚举语义重叠（已修复 2026-05-06，F091）

> 原记录：4 个状态枚举（TaskStatus / WorkerExecutionStatus / WorkerRuntimeState / WorkStatus）。F091 实施期间发现 `WorkerExecutionStatus` 实际不存在（原审计记错），实测 3 个枚举。

**已修复**（F091 5 commits）：
- 3 个状态枚举（TaskStatus 10 值 / WorkerRuntimeState 6 值 / WorkStatus 13 值）建跨枚举映射函数 + 4 module-level dict（在 `delegation.py` 邻接 WorkStatus）
- `work_status_to_task_status` 对 `MERGED / ESCALATED / DELETED` 显式 raise ValueError（语义不能压扁，调用方必须显式处理）
- F090 Phase 1 butler 死代码已删（store 层 `AgentRuntimeRole(row["role"])` 遇 legacy "butler" 行 raise → 改用 `normalize_*` 兜底）
- F090 D1 读取端实测 4 处真实 reader（非原 22 处）已切换到 `runtime_context` 优先 + metadata fallback（F100 移除 fallback 逻辑、F112 删除两 helper 残留死形参后，已无 metadata fallback）

---

## 14.9 F084-F088 完成审计（已修复短板）

> M5 启动前的基础设施修复，2026-05+ 与 M5 同期落地。

### ✅ F084: Context + Harness 全栈重构（仿 Hermes Agent 模式）

替代 F082 的根本方案。详见 [codebase-architecture/harness-and-context.md](../codebase-architecture/harness-and-context.md)。

**Harness 层**：中央 ToolRegistry（数据驱动 entrypoints）+ ToolsetResolver + ThreatScanner（17+ pattern + invisible Unicode）+ SnapshotStore（冻结快照 + Live State 二分，保护 prefix cache）+ ApprovalGate（session allowlist + SSE）+ DelegationManager（max_depth=2 / max_concurrent=3）。

**Context 层**：USER.md 是 SoT，OwnerProfile 退化为派生只读视图；`user_profile.update/read/observe` 三工具 + Memory Candidates API（promote/discard/bulk_discard with atomic claim + skipped_ids）+ Web UI 红点 badge。

**WriteResult 通用回显契约**：18+ 写工具 return type 强制 WriteResult 子类，注册期 fail-fast；保留 task_id / memory_id / run_id 等关联键不压扁。

**退役**：BootstrapSession / BootstrapOrchestrator / UserMdRenderer / bootstrap_integrity / bootstrap_commands CLI（净删 ~2400 行 dead code）。重装路径：清 `~/.octoagent/data + behavior` + `octo update` 重启。

### ✅ F085: capability_pack 拆分（已记录于 §14.8 A1）

47 个工具 handler 迁移到 `builtin_tools/` 子包，按域拆分；CapabilityPackService 从 5112 → 2138 行（-58%），退化为编排层。

### ✅ F086: APScheduler 框架增强

为 F102 Routine 提供 cron 注册基础；与现有 `SchedulerService` 共享调度框架。

### ✅ F087: Agent e2e Live Test Suite

替换旧 `test_acceptance_scenarios.py` 5 域循环为 13 能力域 e2e_live 套件。详见 [codebase-architecture/e2e-testing.md](../codebase-architecture/e2e-testing.md)。

**OctoHarness 抽离**：暴露 4 个 DI 钩子（`credential_store` / `secret_store` / `transport_factory` / `clock`）；内置 120s ProviderRouter timeout + 30s SIGALRM 单测 watchdog。

**13 能力域**：smoke 5（#1 工具调用基础 / #2 USER.md 全链路 / #3 冻结快照 / #11 ThreatScanner block / #12 ApprovalGate SSE）+ full 8（Memory promote / Perplexity MCP / Skill / Graph Pipeline / delegate_task / max_depth / A2A / Routine cron）。

**Hermetic 隔离**：双 autouse fixture 重置 5 类凭证 env / 4 个 OCTOAGENT_* 路径 env / 5 项 module 单例。

**pre-commit hook**：`make install-hooks`（worktree-aware）→ commit 自动跑 `pytest -m e2e_smoke` 180s portable watchdog；`SKIP_E2E=1` 紧急 bypass。

**不变量**：≥ 3026 passed / 0 regression；smoke 5x 循环 4s/iter；SC-7 跑前后 USER.md / auth-profiles.json / mcp-servers/ sha256 完全一致。

### ✅ F088: Module Singletons

测试 hermetic 隔离单例清单维护（`MODULE_SINGLETONS.md`），为 F087 e2e_live 提供 fixture 基础。

## 14.10 F090-F092 类型系统 / 状态机 / Delegation 重构审计

> M5 阶段 0（架构债前置清理，严格串行）。

### ✅ F090: Type System & Naming Cleanup（D1/D2/D5）

**已修复**（保守路径偏离 + Phase 漏做收口到 F091）：
- D1 `metadata flag` → `RuntimeControlContext` 显式字段（**保守双轨**：写入端切换 + 读取端推迟到 F091/F100）
- D2 `AgentProfile + kind` 字段加入（**WorkerProfile 类完全保留**，完全合并推迟到 F107）
- D5 `WorkerSession` → `WorkerDispatchState` **重命名**（发现是 dispatch 瞬时状态而非 session，比原计划"合并进 AgentSession + kind"更对）
- Phase 1 butler 残留 migration 函数删除（50 行）顺延到 F091 完成

### ✅ F091: State Machine Unification + F090 残留（D3 + A7）

**已修复**（5 commits + 4 文档制品，3081 passed vs F090 baseline 0 regression）：
- 3 个状态枚举（TaskStatus / WorkerRuntimeState / WorkStatus；**WorkerExecutionStatus 实测不存在**）建跨枚举映射函数 + 4 module-level dict
- `MERGED / ESCALATED / DELETED` 显式 raise ValueError（语义保留）
- F090 Phase 1 butler 死代码已删；F090 D1 读取端实测 4 处真实 reader（非原 22 处）已切换
- **Final Review 抓到 3 条真 bug**：HIGH（store 层 legacy "butler" 行 raise）+ MED（TaskService → LLMService split-brain）+ MED（is_recall_planner_skip default fallback 不等价）

**Phase 顺序优化**：B → A → C → D（先简后难，删 butler 死代码最简单先做）

### ✅ F092: DelegationPlane Unification（D4）

**已修复**（5 commits + 1 docs，3100 passed vs F091 baseline +19）：
- 主路径 `plane.spawn_child` 统一 spawn 编排入口
- `DelegationManager` production 构造从 5+ 处 → 1 处（plane.py:1058）
- `capability_pack._enforce_child_target_kind_policy` 提为 public（保留 Worker→Worker 禁止待 F098 解绑）
- **3 条豁免路径显式归档**：`apply_worker_plan` / `work.split` / `spawn_from_profile`
- 新增 `SpawnChildResult` 三态（written / rejected / launch_raised）+ `emit_audit_event` 参数控制 SUBAGENT_SPAWNED 写入

**Codex 4 次 review 17 finding 全闭环**：pre-Phase 3 high+3 medium / per-Phase A 1 high+1 medium / per-Phase C 3 high+2 medium+1 low / Final cross-Phase 0 high+2 medium+1 low 推迟 F107。

## 14.11 F093-F096 Worker 完整对等审计

> M5 阶段 1（Agent 完整上下文栈对等，详见 [agent-collaboration-philosophy.md](agent-collaboration-philosophy.md) H2）。

### ✅ F093: Worker Full Session Parity（D6 顺手）

**已修复**（5 commits，3116 passed vs F092 baseline +35）：
- Worker turn 写入 `AgentSession`（baseline 7 跳 grep 验证已通）+ `rolling_summary` / `memory_cursor` 字段持久化 round-trip OK
- 新增 `AGENT_SESSION_TURN_PERSISTED` event（5 字段 payload）
- D6 拆分选最小（agent_context.py 4112→4008，抽出 turn-writer mixin）

**Plan 阶段重要 pattern**：实测发现 baseline 已大部分通（5 Gap 中 4 个已通），F093 实际 90% 是补单测 + 拆分。

### ✅ F094: Worker Memory Parity

**已修复**（8 commits，3029 passed vs F093 baseline 0 regression / +40 测试）：
- `AGENT_PRIVATE` namespace 真生效（Worker 路径）；main direct 保留 `PROJECT_SHARED`（完整对等留 F107，避免破坏 main direct baseline）
- 字段名校正：`RecallFrame.agent_runtime_id`（不是 `agent_id`）；审计真实路径 `AgentProfile.profile_id → AgentRuntime.profile_id → RecallFrame.agent_runtime_id`
- recall preferences 改从 AgentProfile 读
- 废弃 `WORKER_PRIVATE` 路径（合并进 `AGENT_PRIVATE`）
- `migrate-094` CLI no-op 实现（baseline 数据已干净，0 条迁移项）

### ✅ F095: Worker Behavior Workspace Parity

**已修复**（5 commits + 6 review，3191 passed vs F094-merged baseline 0 net regression）：
- `_PROFILE_ALLOWLIST[WORKER]` 5 → **8 文件**：`{AGENTS, TOOLS, IDENTITY, PROJECT, KNOWLEDGE, USER, SOUL, HEARTBEAT}`（**用户 GATE_DESIGN v0.2 翻转**：去 BOOTSTRAP 加 USER，理由：BOOTSTRAP 是主 Agent 用户首次见面对话脚本违反 H1；USER 是用户长期偏好对 Worker 有价值）
- 修复 baseline 隐性 bug：原 envelope `share_with_workers AND` 子句剥离 IDENTITY → IDENTITY.worker.md 模板渲染了 Worker LLM 永远看不到
- `SOUL.worker.md` / `HEARTBEAT.worker.md` worker variant 模板新建
- `share_with_workers` 字段保留（UI 用），envelope 去过滤
- `BehaviorPack.pack_id` hash 化 + `BehaviorPackLoadedPayload` schema + sync helper

### ✅ F096: Worker Recall Audit & Provenance（阶段 1 收尾）

**已修复**（7 commits，cc64f0c push origin/master）：
- `list_recall_frames` audit endpoint（过滤维度：agent_runtime_id / agent_session_id / namespace / project_id / 时间窗）
- `MEMORY_RECALL_COMPLETED` 同步路径 emit（F094 仅 delayed recall path 覆盖）
- `BEHAVIOR_PACK_LOADED` EventStore 接入（F095 推迟项）
- `BEHAVIOR_PACK_USED` 新增（dispatch e2e 集成测）
- **AC-7b 四层 audit chain 实测通过**：`AgentProfile.profile_id ↔ AgentRuntime.profile_id ↔ BEHAVIOR_PACK_LOADED.agent_id ↔ RecallFrame.agent_runtime_id`

**Phase E 显式推迟**：frontend agent 视角 UI（5 AC / 4 组件 / tests / ~300 行）作为独立 Feature 或 F107 顺手清。

## 14.12 F097-F100 委托模式两路分离审计

> M5 阶段 2（详见 [agent-collaboration-philosophy.md](agent-collaboration-philosophy.md) H3）。

### ✅ F097: Subagent Mode Cleanup（H3-A 临时 Subagent）

**已修复**（8 commits，4441a5a push origin/master，3355 passed +103 vs F096 baseline 0 regression）：
- 显式建模 `SubagentDelegation` Pydantic model
- ephemeral `AgentProfile (kind=subagent)`
- `SUBAGENT_INTERNAL` session 路径（与 A2A receiver session 区分）
- cleanup hook + `SUBAGENT_COMPLETED` event emit
- `RuntimeHintBundle` surface 拷贝
- Memory α 共享引用（caller `AGENT_PRIVATE`）
- AC-AUDIT-1 + AC-COMPAT-1 audit chain 验证通过

**Final review 2 high 归档到 F098**：①`USER_MESSAGE` event 复用为 control_metadata 承载体污染 `latest_user_text`（涉及 ContextCompactionService 等多 consumer）；②`_ensure_agent_runtime` 用三元组复用 active runtime → ephemeral subagent 复用 caller worker runtime audit 混叠。

### ✅ F098: A2A Mode + Worker↔Worker（H3-B A2A 真 P2P）

**M5 最复杂 Feature**。已修复（15 commits，c2e97d5 push origin/master）：
- **5 项推迟全闭环**：
  - P1-1 USER_MESSAGE 复用污染 → 引入 `CONTROL_METADATA_UPDATED` event
  - P1-2 ephemeral runtime 独立路径
  - P2-3 atomic 事务边界（session.save + SUBAGENT_COMPLETED 同事务）
  - P2-4 终态统一 cleanup hook + 实例级 callback
  - AC-F1 worker_capability audit chain
- **核心主责**：A2A source+target 双向独立加载；**删除 `_enforce_child_target_kind_policy`（关闭 D14 Worker↔Worker 硬禁止）**；`BaseDelegation` 公共抽象提取
- **D7 真实施**（用户选 B 撤销推迟）：`A2ADispatchMixin` 15 helpers（972 行）抽到 `dispatch_service.py`，orchestrator.py 3623→2733 行（-890 行，行为零变更）

**Phase D post-review 抓到 high pattern**：baseline 用 `turn_executor_kind` 派生 A2A source role（target 侧字段），主 Agent 派 worker 时 source 误判 worker；修复：仅信任显式 `envelope.metadata.source_runtime_kind` 信号。

### ✅ F099: Ask-Back Channel + Source Generalization

**已修复**（8 commits，049f5aa push origin/master，3450 passed +95 vs F098 baseline 0 regression）：
- **三工具体系**：`worker.ask_back` / `worker.request_input` / `worker.escalate_permission`（`ask_back_tools.py`），统一 emit `CONTROL_METADATA_UPDATED` 审计事件
- **`source_runtime_kind` 枚举扩展**：`source_kinds.py` 集中 5 个常量（MAIN/WORKER/SUBAGENT/AUTOMATION/USER_CHANNEL）+ `KNOWN_SOURCE_RUNTIME_KINDS` frozenset
- **N-H1 修复**（re-review 抓到）：`is_caller_worker` resume 持久化通过 `CONTROL_METADATA_UPDATED` 事件机制（worker_runtime emit / task_runner attach_input 桥接 / connection_metadata TASK_SCOPED_CONTROL_KEYS）
- ApprovalGate SSE 路径复用：`escalate_permission` 走现有 `approval_gate.request_approval()` + `wait_for_decision()`（production 接入留 F101）

**3 轮 Codex review pattern**：Final → re-review 抓 1 新 HIGH（N-H1）→ re-re-review N-H1 PARTIAL + 1 新 MED 归档 F101。每次修复都可能引入新问题，至少 2 轮 review 才能收敛到 0 HIGH。

### ✅ F100: Decision Loop Alignment（H1 主 Agent override）

**已修复**（7 commits，182e9ed push origin/master，1469 passed vs F099 baseline 0 regression）：
- `RuntimeControlContext.force_full_recall: bool = False` 字段（H1 override）
- `RecallPlannerMode="auto"` 实际语义启用（按 delegation_mode 自动决议 main_inline/worker_inline→skip / main_delegate/subagent→full）
- FR-H minimal trigger 接入（`metadata["force_full_recall"]` hint）
- **F090 D1 双轨彻底收尾**（Phase E1/E2 移除 orchestrator metadata 写入 + helper fallback，unspecified→False 与 baseline 等价）
- HIGH-1 修复：patched runtime_context 同步 metadata[RUNTIME_CONTEXT_JSON_KEY] 防 stale unspecified seed
- HIGH-2 修复：spec AC-5/FR-E 与 ask_back resume 实际行为对齐

`supports_single_loop_executor` 类属性保留（F091 mock fixture 依赖，与原 prompt 要求相反但实测必须）。

## 14.13 F101-F102 用户感知 ROI 审计

> M5 阶段 3。

### ✅ F101: Notification + Attention Model（范围扩大整合）

**已修复**（8 commits，74c9ab3 push origin/master，3571 passed +21 vs F100 baseline 0 regression）。7 Phase + 1 SKIP（Phase E）。**10 轮 Codex review 收敛**（pre-impl + Phase A + Phase B v1→v4 + Phase C v1→v3 + Phase D v1→v2 + Final），33 HIGH + 16 MED 全闭环或归档下游。

**核心产出**：
- `NotificationService` 四级优先级（CRITICAL/HIGH/MEDIUM/LOW）+ quiet hours discard + USER.md SoT + dismiss 跨通道统一 + Telegram callback + Web API + sha256 notification_id
- `NOTIFICATION_DISPATCHED` 新 EventType（每条 notification 含 quiet hours 内被过滤的都写 event_store）
- `WAITING_APPROVAL` 状态机改造（task_runner 单 owner + CAS + 双注册桥接 ApprovalManager + startup recovery）
- ApprovalGate SSE production 接入（escalate_permission 真闭环）
- `force_full_recall` producer（chat_control_metadata 持久化 + `TURN_SCOPED_CONTROL_KEYS` 白名单 + ENV-aware threshold）
- ask_back 三工具顺手清（FR-C5 非 worker guard + M-1 broad-catch + FR-C7 source_kinds `__all__` + AC-C4 完整事件链 e2e）

**承接 F099 7 项推迟全闭环**（F3 HIGH state machine / F5 PARTIAL / ApprovalGate sse production / AC-E1 e2e / N-H1 PARTIAL resume defense-in-depth / M-1 broad-catch / N-L1 LOW）。

**推迟项**：dismiss 持久化（重启清空 LOW）**→ 已由 F116 实现**；FR-D4 API 显式参数、FR-E1 control_plane 参数仍推迟 F107。

**Phase E SKIP 理由**：D8 control_plane DI 顺手清推迟 F107（避免 F101 范围进一步爆炸）。

### ✅ F102: Proactive Followup（Hermes Routine）

**已修复**（8 commits，9185862 push origin/master，74 新测试 + 联合回归 148 passed）。5 Phase + 17 AC + 16 FR + 10 SD 全闭环。

**核心产出**：
- `DailyRoutineService` 主体（cron 触发 + 9 步执行 + LLM/fallback）+ `DailyRoutineConfig`（USER.md 3 字段解析）
- 4 新 EventType（`ROUTINE_TRIGGERED / COMPLETED / FAILED / SKIPPED`）挂在 `_daily_routine_audit` task
- `task_store.list_tasks_in_time_range` 方法（UTC 归一化，SD-10 时区语义）
- USER.md +3 字段（`daily_summary_time` / `routine_active` / `summary_channels`）
- F101 `NotificationService.notify_task_state_change` 加 `channels` 可选参数（向后兼容）
- LLM token budget 截断（`max_input ≤ 2000` 字符 + `max_output ≤ 512` token）+ deterministic fallback
- **Self-discovered HIGH bug 自修**：`_user_timezone` 字段未更新（`OCTOAGENT_USER_TIMEZONE` env + `zoneinfo` 兜底，4 新测试覆盖）

**范围排除**：WeeklyRoutine 不纳入 F102；dismiss 持久化（已由 F116 实现，原推迟 F107）。

**spec/code 覆盖缺口洞察**：spec 隐性假设 USER.md 有 timezone 机器可读字段（实际只有"时区/地点"人类可读）——future Feature 应明示 USER.md 机器可读字段清单（见 module-design.md §9.14 Context Layer）。

---
