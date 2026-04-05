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

### Subagent 设计评估

整体合理。独立 AsyncioTask + 独立 SkillRunner + A2A 通信 + `kill_subagent` 优雅清理。资源限制（max_steps=100, max_duration=1800s）比 Worker（200, 7200s）更保守，符合预期。

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

### 🟠 A7: 4 个状态枚举语义重叠

TaskStatus(10 值)、WorkerExecutionStatus(3)、WorkerRuntimeState(6)、WorkStatus(13) 中 SUCCEEDED/FAILED/CANCELLED 重复定义。

---
