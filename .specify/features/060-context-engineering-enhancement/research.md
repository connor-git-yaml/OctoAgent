# Technical Research: 060 Context Engineering Enhancement

**Date**: 2026-03-17
**Method**: codebase-scan（代码分析内联于 spec.md 和本文档）

---

## Decision 1: Token 估算算法选择

### 背景

当前 `estimate_text_tokens()` 使用 `len(text) / 4`（context_compaction.py:533-538），对英文合理但对中文系统性低估约 50-100%。中文平均 1.5-2 chars/token（CL100K tokenizer 实测），而 `len/4` 假设 4 chars/token。

### Decision

采用双层方案：tiktoken 精确计算 + CJK 感知字符估算 fallback。

### Rationale

1. tiktoken（CL100K encoding）对中英文混合内容误差 < 5%，是最精确的方案
2. tiktoken 首次加载 ~100ms，之后每次 < 1ms，性能开销可接受
3. tiktoken 是纯 Python 库（有 Rust 加速），在大多数环境可安装
4. 作为 fallback，CJK 感知字符估算 `len(text) / (4*(1-r) + 1.5*r)` 误差 < 30%，优于当前 100%

### Alternatives Rejected

| 方案 | 拒绝理由 |
|------|---------|
| 固定 `len/2` | 对英文过度估算（英文实际 ~4 chars/token），混合内容不准 |
| 固定 `len/3` | 中间值但两端都不精确 |
| sentencepiece tokenizer | 模型绑定太强，需要为不同 provider 维护不同 tokenizer |
| 仅 CJK 感知字符估算 | 误差 ~20-30%，在 token 预算紧张时可能不够精确 |

---

## Decision 2: ContextBudgetPlanner 放置位置

### 背景

BudgetPlanner 的调用方在 `task_service.py`，消费方跨 `ContextCompactionService`（接收 conversation_budget）和 `AgentContextService`（接收 loaded_skills_content + budget_allocation）。

### Decision

独立模块 `context_budget.py` 放在 `gateway/services/` 下。

### Rationale

1. BudgetPlanner 是压缩层和装配层的上游协调者，放入任一下游（context_compaction.py 或 agent_context.py）都违反单一职责
2. context_compaction.py 当前已 597 行，继续膨胀不利于维护
3. 独立模块便于单元测试（不依赖 SQLite 等 store）

### Alternatives Rejected

| 方案 | 拒绝理由 |
|------|---------|
| 放在 context_compaction.py 内部 | 该文件已 597 行，且 BudgetPlanner 不属于"压缩"职责 |
| 放在 agent_context.py 内部 | 该文件已 4200+ 行，且 BudgetPlanner 不属于"装配"职责 |
| 放在 packages/core | BudgetPlanner 依赖 SkillDiscovery（gateway 层），放 core 会造成反向依赖 |

---

## Decision 3: Compressed 层分组策略

### 背景

Agent Zero 使用四层结构 Message -> Topic -> Bulk -> Summary，Topic 层按消息轮次划分（非语义分析）。Spec 要求 MVP 的 Compressed 层需要定义分组粒度。

### Decision

MVP 使用固定轮次窗口（4 turns = 2 轮 user+assistant 对），不做话题语义分割。

### Rationale

1. Agent Zero 的 Topic 层也是按消息轮次划分，验证了该策略在实际场景中有效
2. 语义话题检测（embedding 相似度 / NLP 分割）增加延迟和复杂度，MVP 不需要
3. 固定窗口实现简单、行为可预测、易于调试
4. 后续可升级：在每组压缩的 prompt 中添加"如果本组包含多个不同话题，分别摘要"的指令

### Alternatives Rejected

| 方案 | 拒绝理由 |
|------|---------|
| embedding 相似度话题检测 | 需要额外 embedding 调用，增加延迟和成本 |
| 用户消息开始新话题 | 实际对话中用户常在同一消息切换话题，边界不可靠 |
| 动态窗口（按 token 数） | 实现复杂度高，且 token 数变化不代表话题切换 |

---

## Decision 4: 异步压缩并发控制

### 背景

后台 asyncio.Task 写入 `AgentSession.rolling_summary` 和 `metadata["compressed_layers"]` 时，可能与 `record_response_context()` 的 rolling_summary 更新并发。

### Decision

per-session `asyncio.Lock`，写入前获取锁。

### Rationale

1. 当前系统是单进程 async 架构，asyncio.Lock 足够（无需数据库级锁）
2. 锁粒度为 session 级，不同 session 不互相阻塞
3. 超时由 `asyncio.wait_for(10s)` 保护，超时后释放锁
4. 实现简单、行为可预测

### Alternatives Rejected

| 方案 | 拒绝理由 |
|------|---------|
| 数据库级锁（SQLite EXCLUSIVE） | 过重，影响其他 session 的写入 |
| 无并发控制 | 可能导致 rolling_summary 数据覆盖 |
| 乐观锁（版本号） | 需要额外的 version 字段和重试逻辑，增加复杂度 |
| 全局单锁 | 不同 session 不需要互斥，全局锁降低并发性 |

---

## Decision 5: Skill 注入迁移方案

### 背景

当前 `LLMService._build_loaded_skills_context()` 在 `_try_call_with_tools()` 中追加 Skill 内容到 `base_description`，发生在 `_fit_prompt_budget()` 之后，完全游离于预算体系之外。

### Decision

Skill 内容移到 `_build_system_blocks()` 中作为独立的 `LoadedSkills` 系统块，参与 `_fit_prompt_budget()` 的 token 计算。

### Rationale

1. 作为系统块参与预算计算，消除预算漏洞
2. Skill 内容在所有请求中可见（包括无工具的纯聊天），这是更正确的行为
3. BudgetPlanner 可以预估 Skill 预算，减少 `_fit_prompt_budget()` 降级概率

### Alternatives Rejected

| 方案 | 拒绝理由 |
|------|---------|
| 在 _fit_prompt_budget 之后做 token 检查并截断 | 仍是补偿性设计，不解决根本的预算断裂问题 |
| 在 LLMService 中预留固定 Skill 预算 | Skill 数量和内容长度不固定，固定预留不精确 |
| 让 _fit_prompt_budget 增加 Skill 搜索维度 | 参数空间爆炸（从 ~240 到 ~960 种组合），搜索效率下降 |

### 影响面

- `_build_loaded_skills_context()` 当前只在 `_try_call_with_tools()` 路径执行（有挂载工具时）
- 迁移后，Skill 内容在所有请求中注入（包括无工具的纯聊天），这是正确行为
- 需确保从 LLMService 中移除追加逻辑，避免双重注入

---

## Decision 6: rolling_summary 迁移兼容策略

### 背景

060 将 `rolling_summary` 语义从"全部历史扁平摘要"升级为"Archive 层骨架摘要"。旧 session 的 rolling_summary 是旧格式。

### Decision

通过 `AgentSession.metadata["compaction_version"]` 字段区分版本，读取时按版本解析。

### Rationale

1. metadata 为 JSON 列，增加字段零成本（不需要 DDL 迁移）
2. 旧 session 的 rolling_summary 内容本身就可以作为 Archive 层使用（都是历史摘要）
3. 读取兼容逻辑简单：v1 = 旧格式（整体作为 Archive），v2 = 新格式

### Alternatives Rejected

| 方案 | 拒绝理由 |
|------|---------|
| 一次性迁移所有旧 session | 需要批量更新脚本，且旧 session 可能已关闭 |
| 新增独立 `archive_summary` 字段 | 需要 DDL 变更，且 rolling_summary 字段会浪费 |
| 基于 rolling_summary 内容格式自动判断 | 不可靠，旧摘要可能碰巧包含类似新格式的内容 |

---

## Decision 7: progress_note 存储方案

### 背景

进度笔记需要持久化，且绑定到 task_id + agent_session_id。

### Decision

使用 Artifact Store（type: `progress-note`），每条笔记一个 Artifact。

### Rationale

1. Artifact Store 已有完整的 CRUD 基础设施和事件审计链
2. Artifact 天然绑定 task_id，可通过 API 按 type 过滤查询
3. JSON part 支持结构化内容
4. Butler 可通过现有 Artifact API 查询 Worker 的进度笔记（Constitution #8）

### Alternatives Rejected

| 方案 | 拒绝理由 |
|------|---------|
| 写入 AgentSession.metadata | metadata 是单个 JSON blob，大量笔记会导致膨胀 |
| 写入 Memory SoR | 违反 Constitution #12（模型不可直接写 SoR） |
| 新建独立表 | 增加基础设施复杂度，MVP 不需要 |
| 写入 Event payload | Event 是 append-only 且不便于查询特定类型 |

---

## Decision 8: 前端 compaction alias 展示方案

### 背景

Settings 前端需要展示 `compaction` 别名，让用户可以绑定轻量模型。

### Decision

使用现有 alias 编辑器，仅为 `compaction` alias 增加辅助提示文本。

### Rationale

1. 现有 SettingsProviderSection.tsx 已有完整的 alias CRUD UI
2. `compaction` 只是 alias 列表中的一个条目，不需要独立配置区域
3. 增加辅助文本（用途说明 + fallback 链路）足以引导用户

### Alternatives Rejected

| 方案 | 拒绝理由 |
|------|---------|
| 独立的 "Compaction Settings" 配置面板 | 过度设计，一个 alias 不需要专属面板 |
| 自动推荐绑定 | 系统不知道用户可用的轻量模型，推荐可能不准确 |
