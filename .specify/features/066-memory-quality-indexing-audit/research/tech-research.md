# 技术调研报告: Memory 提取质量、索引利用与审计机制优化

**特性分支**: `claude/competent-pike`
**调研日期**: 2026-03-19
**调研模式**: 在线（代码库深度分析）
**产品调研基础**: [独立模式] 本次技术调研未参考产品调研结论，直接基于需求描述和代码上下文执行

---

## 1. 调研目标

**核心问题**:
- Agent 如何利用记忆——recall 机制在什么时机触发、结果如何注入 system prompt？
- Memory UI 当前只支持只读查看还是已有编辑/删除能力？审计能力完备度如何？
- Agent 的记忆查询工具是否存在按 subject_key / partition / derived_type 等维度精确查询的缺口？

**需求 MVP 范围（直接来自需求描述）**:
- 优化记忆提取质量（recall precision / recall 覆盖）
- 改善索引利用方式（Agent 侧工具调用便利性）
- 增强审计机制（用户可查看、编辑、删除记忆；proposal 审计链完整）

---

## 2. 调研方向 1: Agent 如何利用记忆（索引与注入）

### 2a. OctoAgent 当前 recall 机制

#### `_execute_recall_memory()` 工作流程

位于 `agent_context.py`（第 3510-3780 行），核心函数签名：

```python
async def _execute_recall_memory(
    self, *, request, project, workspace, memory_service,
    agent_profile, agent_runtime, agent_session,
    memory_namespaces, query, recall_plan=None,
) -> tuple[list[MemoryRecallHit], list[str], list[str], dict[str, Any]]
```

**执行流程**：
1. `_build_memory_scope_entries()` 从 `memory_namespaces` 解析 `scope_ids`
2. `_resolve_memory_prefetch_mode()` 决定 prefetch 模式
3. 根据模式选择不同路径执行 recall

#### 三种 Prefetch 模式

| 模式 | 触发条件 | 行为 |
|------|---------|------|
| `detailed_prefetch` | 显式配置 | 直接调用 `memory_service.recall_memory()`，命中结果渲染进 system prompt（最多 4 条，含 preview） |
| `hint_first` | Worker / 自定义 Agent 默认 | 如有 `recall_plan`（RecallPlanMode.RECALL），执行一轮精确 recall；否则只返回 scope 信息，不预取命中 |
| `agent_led_hint_first` | Butler 默认 | 同 `hint_first`，但追加 guidance 提示 Agent 主动调用 memory 工具 |

#### Prefetch recall vs Agent-led recall

- **Prefetch recall**: 在 context assembly 阶段自动触发（`_execute_recall_memory`），结果通过 `_render_memory_recall_block()` 写入 system prompt 的 ContextFrame
- **Agent-led recall**: Agent 在对话过程中主动调用 `memory.recall` / `memory.search` / `memory.read` 工具，通过 `record_delayed_recall_state()` 记录延迟 recall 状态

#### RecallFrame 渲染位置

渲染为 system prompt 中的结构化 block（第 4623-4676 行）：

- `_render_memory_runtime_block()`: 永远注入，告知 Agent 当前 memory mode / scopes / available_tools
- `_render_memory_recall_block()`: 仅当 `include_detailed_recall=True` 时渲染完整命中；否则渲染 hints

格式示例：
```
MemoryRecall:
scopes: scope_1, scope_2
- [work] 用户偏好/编程语言: Connor 偏好 Python 和 TypeScript...
  citation: 来自 2026-02-15 对话
  preview: 详细内容摘要...
```

#### 四个 Memory 工具参数与使用场景

| 工具 | 参数 | 场景 |
|------|------|------|
| `memory.read` | `subject_key`, `scope_id`, `project_id`, `workspace_id` | 读取特定 subject 的 current + history 版本，精确查询 |
| `memory.search` | `query`, `scope_id`, `partition`, `layer`, `limit` (max 50) | 文本搜索，支持 scope/partition/layer 筛选 |
| `memory.recall` | `query`, `scope_id`, `limit` (max 8), `allow_vault`, `post_filter_mode`, `rerank_mode`, `subject_hint`, `focus_terms` | 生成结构化 recall pack（含 query 扩展、命中、citation、backend truth、hook trace） |
| `memory.write` | `subject_key`, `content`, `partition`, `evidence_refs`, `scope_id` | 持久化长期记忆（SoR 记录），系统自动判断新增/更新 |

另有辅助工具：
- `memory.citations`: 读取 subject 的证据链引用

### 2b. OpenClaw 的记忆利用机制

**`memory-search.ts` 配置体系**:
- 配置驱动的搜索参数（不是硬编码），支持 per-agent 覆盖
- 搜索参数：`maxResults=6`, `minScore=0.35`, hybrid search（70% vector / 30% BM25）
- 支持 MMR（Maximal Marginal Relevance）去重和 temporal decay（半衰期 30 天）
- 支持多数据源：`memory`（MEMORY.md 文件目录）和 `sessions`（会话历史）

**MEMORY.md 注入机制**（来自 `system-prompt.ts`）:
- 在 system prompt 中注入 Memory Recall 指令段
- 指令文本大意：在回答任何关于历史工作、决策、日期、偏好等问题前，先对 `MEMORY.md` + `memory/*.md` 执行 `memory_search`，然后用 `memory_get` 拉取需要的行
- 这是 **system prompt 级指令驱动**，而非自动注入搜索结果

**memU bridge 机制**（`memu_bridge.py`）:
- 替代 OpenClaw 内建的 qmd 搜索引擎
- 拦截 `query` 命令，转发到 `memu_retrieve.py`（pgvector + BM25 + reranker pipeline）
- 跳过低价值查询（heartbeat、cron error 等 boilerplate）
- 返回 qmd 兼容 JSON 格式（含 docid、score、snippet）
- 通过在 qmd 的 SQLite index 中注册文档 ID，使 OpenClaw 能解析文件路径和显示 citations

### 2c. Agent Zero 的记忆利用机制

**自动 recall 扩展**（`_50_recall_memories.py`）:
- 每 N 轮迭代自动触发（`memory_recall_interval` 可配置，默认每次循环）
- 由 LLM 生成搜索 query（可选关闭 `memory_recall_query_prep`）
- 分两路搜索：general memories + solutions（不同 area 过滤）
- 可选 AI 后过滤（LLM 验证 relevance）
- 命中结果注入 `extras_persistent["memories"]` / `extras_persistent["solutions"]`
- 通过 prompt 模板 `agent.system.memories.md` 渲染到 prompt 中

**`memory_load` 工具**:
- 简单的 similarity 搜索：`query`, `threshold=0.7`, `limit=10`, `filter`
- Agent 主动调用，返回纯文本拼接

**Memory Dashboard API**（`memory_dashboard.py`）:
- 支持操作：search / delete / bulk_delete / update / get_memory_subdirs / get_current_memory_subdir
- 搜索支持 area 过滤（`filter=f"area == '{area_filter}'"`)
- 完整的 CRUD 操作

### 2d. 对比分析

| 维度 | OctoAgent | OpenClaw | Agent Zero |
|------|-----------|----------|------------|
| 自动 recall 时机 | Context assembly 阶段 | System prompt 指令驱动 Agent 主动搜索 | 每 N 轮自动 + Agent 主动 |
| recall 注入方式 | RecallFrame 渲染到 system prompt | Agent 自行调用 memory tool | extras_persistent 注入 prompt |
| query 来源 | 用户输入 / RecallPlan | Agent 自行决定 | LLM 从对话历史生成 |
| 搜索后过滤 | keyword_overlap / heuristic rerank | MMR + temporal decay | LLM 验证 relevance |
| 工具丰富度 | 4 个工具（read/search/recall/write） | 2 个工具（memory_search/memory_get） | 2 个工具（memory_load/memory_save） |
| 结构化元数据 | 丰富（partition/layer/scope/evidence_refs） | 中等（area/timestamp/metadata） | 简单（area/timestamp） |

---

## 3. 调研方向 2: 记忆审计 UI 实现参考

### 3a. OctoAgent 当前 Memory UI 能力

**前端组件**（`frontend/src/domains/memory/`）:
- `MemoryPage.tsx`: 主页面，整合 Hero/Filters/Results/DetailModal
- `MemoryFiltersSection.tsx`: 支持按 scope / 关键词 / layer / partition / limit 筛选
- `MemoryResultsSection.tsx`: 记忆列表，显示 layer/partition/status/version/evidence_refs
- `MemoryHeroSection.tsx`: 概览统计（SoR 数量、fragment 数量、待整理数等）
- `MemoryDetailModal.tsx`: 单条记忆详情查看
- `MemoryRetrievalLifecycleSection.tsx`: 向量索引生命周期管理

**Control Plane Action 清单**:

| action_id | 功能 | 状态 |
|-----------|------|------|
| `memory.query` | 查询记忆列表（支持 scope/query/layer/partition 筛选） | 已实现 |
| `memory.consolidate` | 整理记忆（Fragment -> SoR CONSOLIDATE） | 已实现 |
| `memory.subject.inspect` | 查看单个 subject 的 current + history | 已实现 |
| `memory.proposal.inspect` | 查看 proposal 审计记录 | 已实现 |
| `memory.flush` | 刷新 memory backend | 已实现 |
| `memory.reindex` | 重建索引 | 已实现 |
| `memory.sync.resume` | 恢复同步 | 已实现 |
| `memory.profile_generate` | 生成 profile | 已实现 |
| `memory.export.inspect` | 导出检查 | 已实现 |
| `memory.restore.verify` | 恢复验证 | 已实现 |
| `retrieval.index.start/cancel/cutover/rollback` | 向量索引生命周期管理 | 已实现 |

**缺失的审计操作**:
- **SoR 编辑**: 无 `memory.sor.edit` 或 `memory.sor.update` action
- **SoR 删除**: 无 `memory.sor.delete` action
- **Fragment 删除**: 无单条 fragment 删除 action
- **Proposal 审批/拒绝**: 无 `memory.proposal.approve/reject` action（可从前端触发）

**后端 Store 层能力**:
- `SqliteMemoryStore.update_sor_status()` 可更改 SoR 状态（current -> superseded / archived）
- 但无直接的"编辑 SoR content"或"删除 SoR"API
- `MemoryConsoleService` 提供了丰富的查询能力，但写入路径只有 `propose_write` -> `validate` -> `commit` 三步流程

### 3b. Agent Zero Memory Dashboard 对比

| 操作 | Agent Zero | OctoAgent |
|------|-----------|-----------|
| 搜索/浏览 | search（similarity + area filter） | memory.query（scope/query/layer/partition） |
| 查看详情 | content_full + metadata | memory.subject.inspect |
| 编辑记忆 | update（直接修改 content + metadata） | 不支持 |
| 删除单条 | delete（by memory_id） | 不支持 |
| 批量删除 | bulk_delete（by memory_ids list） | 不支持 |
| 切换子目录 | get_memory_subdirs / set | scope 切换（类似功能） |
| 统计概览 | total_count / knowledge_count | summary（SoR/fragment/vault/proposal 各维度） |
| Proposal 审计 | 无 | memory.proposal.inspect |
| 导出/恢复 | 无 | memory.export / memory.restore |

---

## 4. 调研方向 3: 记忆查询便利性

### 当前工具能力与缺口分析

**`memory.search` 现状**:
- 必须提供 `query` 文本进行搜索
- 支持 `scope_id`、`partition`、`layer` 筛选（已有）
- `limit` 上限 50

**缺失的查询维度**:

| 查询需求 | 现状 | 缺口 |
|---------|------|------|
| 按 subject_key 精确查询 | `memory.read` 支持（但只返回单个 subject 的 current + history） | 无法列出所有 subject_key 或按前缀匹配 |
| 按 partition 筛选 | `memory.search` 的 `partition` 参数支持 | 已有，无缺口 |
| 按 derived_type 筛选 | MemoryStore 的 `query_derived()` 支持 `derived_type` 过滤 | Agent 工具未暴露此能力 |
| 列出所有 subject_key | `MemoryConsoleService.get_memory_console()` 返回 records 列表 | Agent 工具无法获取（只有 Control Plane action `memory.query` 可以） |
| 按 scope 浏览记忆目录 | Control Plane 返回 `available_scopes` | Agent 无法获取可用 scope 列表 |
| 按时间范围筛选 | MemoryStore SQL 支持 `created_at` / `updated_at` | Agent 工具未暴露时间范围参数 |
| 按 confidence 筛选 | SoR 模型有 `confidence` 字段 | Agent 工具未暴露 |
| 按 status 筛选 | MemoryStore 支持 `current` / `superseded` / `archived` 过滤 | Agent 工具未暴露 |

**是否需要 `memory.browse` 工具**:

强烈建议新增。当前 Agent 只能通过 `memory.search(query=...)` 做文本搜索，无法：
1. 浏览"我知道什么"（列出所有 subject_key 或按前缀分组）
2. 按结构化维度筛选（derived_type、confidence 范围、时间范围）
3. 获取 memory 系统概览（各 partition/scope 的记忆数量）

---

## 5. 架构方案对比

### 方案 A: 增量式扩展（工具增强 + UI 审计补齐）

在现有架构上逐步添加能力：
- 新增 `memory.browse` 工具，支持 subject_key 列表、前缀匹配、分组统计
- 扩展 `memory.search` 参数（derived_type、time_range、confidence_range）
- Control Plane 新增 `memory.sor.edit` / `memory.sor.delete` / `memory.sor.archive` action
- 前端 MemoryPage 增加编辑/删除按钮

### 方案 B: 统一记忆查询层（Query DSL + 统一 API）

引入结构化查询 DSL，统一 Agent 工具和 UI 的查询能力：
- 定义 `MemoryQuery` DSL（参考 DerivedMemoryQuery 已有模型）
- 所有 memory 工具共享同一查询引擎
- Agent 工具和 UI 使用完全相同的 API 端点
- 查询结果格式统一（含 pagination / facets）

### 方案对比表

| 维度 | 方案 A: 增量扩展 | 方案 B: 统一查询层 |
|------|--------------|-----------------|
| 概述 | 在现有 4 工具基础上新增/扩展 | 重构为统一查询 DSL |
| 实现成本 | 低（利用现有 Store 能力） | 中高（需设计 DSL、重构工具层） |
| 可维护性 | 中（工具间参数可能不一致） | 高（单一查询入口） |
| 向后兼容 | 完全兼容 | 需要迁移现有工具调用 |
| 扩展性 | 中（每次新增查询维度需改工具签名） | 高（DSL 天然可扩展） |
| Agent 可用性 | 好（工具语义清晰） | 需要 Agent 学习 DSL 语法 |
| 与现有代码兼容 | 完全兼容 SqliteMemoryStore | 需要 adapter 层 |

### 推荐方案

**推荐**: 方案 A（增量式扩展）

**理由**:
1. OctoAgent 已有 4 个语义清晰的 memory 工具，Agent 已学会使用，增量扩展不破坏已有行为
2. `SqliteMemoryStore` 已具备所有需要的底层查询能力（subject_key、partition、derived_type、confidence 等字段都有索引），只需在工具层暴露
3. 方案 B 的 DSL 设计增加了 Agent 的认知负担（需要学习查询语法），且 LLM 对复杂 DSL 的执行可靠性不如语义化工具调用
4. Constitution 要求 "Degrade Gracefully"，增量方案风险更低

---

## 6. 依赖库评估

本次优化主要是**架构层调整**，不需要引入新的外部依赖库。所有功能均基于现有技术栈实现：

| 能力 | 实现方式 | 是否需要新依赖 |
|------|---------|-------------|
| 新 memory 工具 | capability_pack.py 新增 tool_contract | 否 |
| UI 编辑/删除 | Control Plane action + MemoryConsoleService | 否 |
| 结构化查询扩展 | SqliteMemoryStore 已有 SQL 能力 | 否 |
| browse 统计 | SQLite GROUP BY 查询 | 否 |
| 审计链完善 | 复用现有 WriteProposal + EventStore | 否 |

### 与现有项目的兼容性

| 现有依赖 | 兼容性 | 说明 |
|---------|--------|------|
| SQLite WAL | 兼容 | 新查询利用现有索引 |
| Pydantic Models | 兼容 | 扩展现有 model 字段 |
| FastAPI Control Plane | 兼容 | 新增 action handler |
| React + Vite Frontend | 兼容 | 扩展现有 MemoryPage 组件 |
| LanceDB vector backend | 兼容 | memory.browse 不走向量检索路径 |

---

## 7. 设计模式推荐

### 推荐模式

1. **Facade 模式**（用于 `memory.browse`）:
   - 为 Agent 提供一个简化的高层接口，内部组合 MemoryStore 的多个查询方法
   - 返回聚合结果（subject_key 列表 + 分组统计 + 最近更新时间）
   - 参考 Agent Zero 的 `_search_memories` 方法，将多维筛选封装为单一工具

2. **Command 模式**（用于审计操作）:
   - SoR 编辑/删除/归档作为 Control Plane action 执行
   - 每个操作生成 Event 记录（满足 Constitution "Everything is an Event"）
   - 通过 WriteProposal 流程保证操作可审计（满足 "Side-effect Must be Two-Phase"）

3. **Strategy 模式**（用于 recall 质量优化）:
   - 现有的 `post_filter_mode` 和 `rerank_mode` 已是 Strategy 模式
   - 可进一步扩展 recall strategy：按 subject_hint 权重调整、按 temporal decay 调整

### 应用案例

- Agent Zero 的 Memory Dashboard 采用了 Facade 模式：单一 API (`MemoryDashboard.process()`) 通过 action dispatch 支持 search/delete/update 等操作
- OpenClaw 的 `memory-search.ts` 配置体系采用了 Strategy 模式：通过配置切换 hybrid search 权重、MMR、temporal decay 等策略

---

## 8. 技术风险清单

| # | 风险描述 | 概率 | 影响 | 缓解策略 |
|---|---------|------|------|---------|
| 1 | `memory.browse` 工具返回大量 subject_key 时，超出 LLM context window | 中 | 中 | 限制返回条数（默认 20），支持 offset/limit 分页；按前缀分组聚合而非逐条返回 |
| 2 | SoR 编辑/删除破坏证据链完整性 | 中 | 高 | 编辑/删除通过 WriteProposal 流程执行，保留原版本为 superseded 状态，不做物理删除 |
| 3 | 用户通过 UI 误删关键记忆 | 低 | 高 | 删除操作需二次确认；"删除"实为标记 archived，30 天后才物理清除；支持恢复 |
| 4 | recall prefetch 模式切换导致 Agent 行为不一致 | 低 | 中 | 保持默认模式不变（Butler=agent_led_hint_first, Worker=hint_first），新功能不改变默认值 |
| 5 | `memory.search` 新增参数导致已有 Agent 行为回退 | 低 | 低 | 新参数均为可选，默认值保持向后兼容 |

---

## 9. 需求-技术对齐度

### 覆盖评估

| 需求功能 | 技术方案覆盖 | 说明 |
|---------|-------------|------|
| 提升 recall 质量 | 完全覆盖 | 扩展 `memory.recall` 的 subject_hint/focus_terms + browse 补充上下文 |
| 改善索引利用 | 完全覆盖 | 新增 `memory.browse` + 扩展 `memory.search` 参数 |
| 用户查看记忆 | 已有 | `memory.query` + `memory.subject.inspect` |
| 用户编辑记忆 | 未覆盖 -> 需新增 | 新增 `memory.sor.edit` action |
| 用户删除记忆 | 未覆盖 -> 需新增 | 新增 `memory.sor.archive` action |
| Proposal 审计 | 已有 | `memory.proposal.inspect` |
| 按 subject_key 浏览 | 未覆盖 -> 需新增 | 新增 `memory.browse` 工具 |
| 按 derived_type 筛选 | 未覆盖 -> 需新增 | 扩展 `memory.search` 或 `memory.browse` |

### 扩展性评估

当前架构支持以下未来扩展：
- **记忆版本 diff**: SoR 已有 version 字段和 superseded 历史链，可实现版本对比
- **记忆导出/导入**: `memory.export.inspect` / `memory.restore.verify` 已有骨架
- **跨 Agent 记忆共享**: scope 机制已支持 project_shared / butler_private / worker_private
- **记忆自动过期**: 可通过 maintenance pipeline 实现 TTL 策略

### Constitution 约束检查

| 约束 | 兼容性 | 说明 |
|------|--------|------|
| Durability First | 兼容 | SoR 编辑/删除不做物理删除，保留 superseded 版本 |
| Everything is an Event | 兼容 | 所有审计操作通过 Event Store 记录 |
| Tools are Contracts | 兼容 | 新工具遵循 `tool_contract` 装饰器规范 |
| Side-effect Must be Two-Phase | 兼容 | SoR 编辑/删除走 Proposal 流程（Plan -> Gate -> Execute） |
| Least Privilege by Default | 兼容 | vault 记忆的编辑/删除需要额外授权 |
| Degrade Gracefully | 兼容 | browse 工具在 backend 降级时 fallback 到 SQLite 查询 |
| User-in-Control | 兼容 | 编辑/删除需 UI 确认，支持撤销 |
| Observability is a Feature | 兼容 | 操作生成 Event，可在 Control Plane 查看 |

---

## 10. 具体实现建议

### 10a. 新增 `memory.browse` 工具

**参数设计**:
```python
async def memory_browse(
    prefix: str = "",           # subject_key 前缀匹配（如 "用户偏好/"）
    partition: str = "",        # partition 筛选
    scope_id: str = "",         # scope 筛选
    group_by: str = "partition", # 分组维度: partition / scope / prefix
    limit: int = 20,            # 返回条数
    project_id: str = "",
    workspace_id: str = "",
) -> str:
```

**返回格式**:
```json
{
  "groups": [
    {
      "key": "core",
      "count": 12,
      "items": [
        {"subject_key": "用户偏好/编程语言", "summary": "...", "updated_at": "..."},
        {"subject_key": "用户偏好/时区", "summary": "...", "updated_at": "..."}
      ]
    }
  ],
  "total_count": 42
}
```

### 10b. 扩展 `memory.search` 参数

新增可选参数：
- `subject_key_prefix: str = ""` — 按 subject_key 前缀匹配
- `derived_type: str = ""` — 按 derived type 筛选
- `min_confidence: float = 0.0` — 最低置信度阈值
- `status: str = ""` — 按 SoR 状态筛选（current / superseded / archived）

### 10c. 审计操作 API

新增 Control Plane action:
- `memory.sor.edit`: 修改 SoR content（通过 propose_write + SUPERSEDE + 新 SoR 实现）
- `memory.sor.archive`: 将 SoR 标记为 archived（软删除）
- `memory.sor.restore`: 从 archived 恢复为 current

### 10d. 前端 UI 增强

- MemoryDetailModal 增加"编辑"和"归档"按钮
- 编辑触发 `memory.sor.edit` action，显示 before/after diff
- 归档触发 `memory.sor.archive` action，需二次确认
- 增加"已归档记忆"筛选视图

---

## 11. 结论与建议

### 总结

OctoAgent 的 Memory 系统在**存储层**和 **recall pipeline** 上已相当成熟（scope 隔离、SoR/Fragment/Vault 三层模型、evidence_refs 证据链、多 backend 支持）。核心缺口集中在两个方面：

1. **Agent 工具层**：缺少 browse/listing 能力，Agent 只能"搜"不能"看目录"，无法按结构化维度精确筛选
2. **用户审计层**：UI 只有只读查看 + consolidate，缺少编辑/删除/归档操作

三个参考系统的对比揭示了一个共同模式：**记忆系统的价值不仅在于"存"和"搜"，更在于让用户和 Agent 都能方便地"管理"记忆**。Agent Zero 提供完整 CRUD Dashboard，OpenClaw 通过 MEMORY.md 让用户直接编辑文件——OctoAgent 需要补齐这一环。

### 对后续规划的建议

- **优先补齐 memory.browse 工具**: 这是 Agent 侧改善最大的单点突破——让 Agent 知道"我记得什么"
- **SoR 编辑/归档走 Proposal 流程**: 不要绕过现有写入仲裁机制，保证审计链完整
- **recall 质量优化聚焦 focus_terms + subject_hint**: 这两个参数已在 recall pipeline 中支持但未被充分利用
- **前端审计 UI 建议与 Advanced 页面整合**: 编辑/删除是管理操作，放在 Memory 页面的操作栏即可，但需明确告知用户操作后果
