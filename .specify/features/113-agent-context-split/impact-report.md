# F113 影响分析报告（impact-report）

> 目标：`octoagent/apps/gateway/src/octoagent/gateway/services/agent_context.py` 按职责簇拆 mixin
> baseline：origin/master `167b9cf4`（F112 -15 行 + F124 +28 行已合入后的实际内容）
> 分析方式：全部实测（行数脚本 + 调用图脚本 + Explore agent 全仓 grep），非记忆推断

## 1. 目标文件现状（实测）

- **总行数 4600**（与 2026-06-08 集成 review 数字一致）
- 结构：import 区 126 + 常量 25 + `SystemPromptContext` dataclass 33 + module-level 自由函数 324 + 3 个 dataclass 50 + `AgentContextService` 类体 4039
- 类方法数 **61**（含 3 classmethod + `__init__`）；继承 `AgentContextTurnWriterMixin`（F093 已抽，4 方法 210 行）
- F124 改动落点：`render_agent_session_replay_block`（Session-replay 簇）+ `_build_research_handoff_block`（Prompt-assembly 簇）+ import `octoagent.tooling.security_render`。**不新增第 5 关注点**，与集成 review 预判一致

## 2. 职责簇实测核对（4039 行类体逐方法对账，账平）

| 簇 | 方法（行数） | 小计 |
|----|------------|------|
| **基类保留（编排根）** | 类头+`_shared_*` 类属性+3 个 `set_*` classmethod (36)、`__init__` (26)、`build_task_context` (556)、`build_recall_planning_context` (46)、`_build_context_request` (87)、`_resolve_context_bundle` (154) | **905** |
| **EntityEnsureMixin** | `_build_ephemeral_subagent_profile` (26)、`_resolve_agent_runtime_role` (18)、`_build_memory_namespace_id` (12)、`_ensure_agent_runtime` (149)、`_find_existing_session_for_ensure` (49)、`_ensure_agent_session` (256)、`_ensure_memory_namespaces` (219)、`_resolve_agent_profile` (22)、`_ensure_agent_profile` (87)、`_ensure_agent_profile_from_worker_profile` (69)、`_ensure_owner_profile` (18)、`_ensure_owner_overlay` (43)、`_ensure_session_context` (31)、`_load_session_context` (50) | **1049** |
| **MemoryRecallMixin** | `record_delayed_recall_state` (78)、`resolve_project_scope` (8)、`get_memory_retrieval_profile` (11)、`_resolve_project_scope` (18)、`_search_memory_hits` (301)、`_resolve_project_memory_scope_ids` (22)、`_build_memory_scope_entries` (38) | **476** |
| **MemoryExtractionServiceMixin** | `get_memory_service` (9)、`get_consolidation_service` (30)、`get_derived_extraction_service` (21)、`get_tom_extraction_service` (21)、`get_profile_generator_service` (23)、`get_session_memory_extractor` (22)、`_spawn_session_memory_extraction` (52)、`get_reranker_service` (14) | **192** |
| **SessionReplayMixin** | `record_response_context` (181)、`_normalize_session_transcript_entries` (27)、`_agent_session_transcript_entries` (11)、`_agent_session_turn_to_transcript_entry` (18)、`_list_agent_session_turn_transcript_entries` (19)、`build_agent_session_replay_projection` (149)、`_normalize_turn_summary` (7)、`render_agent_session_replay_block` (30)、`_trim_session_replay_projection` (38)、`_append_session_transcript_entries` (36)、`_replace_session_transcript_entries_from_messages` (24)、`record_compaction_context` (104) | **644** |
| **PromptAssemblyMixin** | `_build_system_blocks` (305)、`_build_research_handoff_block` (64)、`_fit_prompt_budget` (161)、`_build_source_refs` (79)、`_memory_hit_payload` (20)、`_render_memory_runtime_block` (20)、`_render_memory_recall_block` (36)、`_append_unique_tail` (8)、`_append_source_refs` (19)、`_summarize_turns` (6)、`_render_list` (5)、`_render_snapshot` (50) | **773** |

对账：36+26+843+1049+476+192+644+773 = **4039** ✅
与审计 A4 对照：Session-replay 644 精确吻合；Entity 1049 ≈ 1075；Memory-service 192 ≈ 203；审计"Memory ~718"实测拆为 Recall 476（差额在 module-level recall 配置函数 ~130 行，归 helpers 文件）。**结论：拆 5 个 mixin + 1 个 helpers 文件**，第 5 簇 Memory-service 成立。

### 调用图关键结论（实测脚本）

- `build_task_context` → 跨 Prompt/Replay/编排根 4 簇；`_resolve_context_bundle` → 跨 Entity/Recall 两簇全家。**确认审计决议：两者 + `build_recall_planning_context` + `_build_context_request` 必须留基类，不可抽**
- 跨 mixin 互调 4 条（Python 多继承同实例运行时无影响，mixin docstring 须声明）：
  - SessionReplay.`record_response_context` → MemoryService.`_spawn_session_memory_extraction` + Entity.`_ensure_session_context`/`_load_session_context`
  - Recall.`_search_memory_hits` → MemoryService.`get_memory_service`/`get_memory_retrieval_profile`
  - PromptAssembly.`_build_system_blocks` → SessionReplay.`render_agent_session_replay_block`
  - Recall.`record_delayed_recall_state` → PromptAssembly.`_append_source_refs`
- `__init__` 调 MemoryService.`get_reranker_service()`（基类 init 调 mixin 方法，多继承合法）
- 61 方法名全唯一，MRO 无遮蔽风险

## 3. 外部契约清单（拆分不可破坏，全部实测）

### 3.1 测试/生产代码类名直调静态方法（mixin 继承后仍可见，安全）
- `AgentContextService._build_memory_scope_entries` ← **packages/core/tests/test_agent_context_store.py（跨包测试）**
- `AgentContextService._memory_hit_payload` ← **task_service.py 生产代码直调**
- `AgentContextService._build_ephemeral_subagent_profile` ← test_agent_context_phase_c / test_behavior_pack_loaded_phase_g
- `AgentContextService._build_research_handoff_block` ← 测试直调

### 3.2 测试实例直调
- `svc.build_agent_session_replay_projection(...)` ← test_worker_session_turn_isolation / test_task_service_context_integration

### 3.3 类属性（必须留主类）
- `_shared_llm_service` / `_shared_provider_router` / `_shared_background_tasks` ← e2e_live/conftest reset + test_conftest_sanity + state_diff + test_session_memory_spawn 直接 get/setattr `AgentContextService.<attr>`

### 3.4 module-level 名字外部 import（移 helpers 后主文件必须 re-export）
- 生产：task_service / orchestrator / agent_session_turn_hook / delegation_plane / dispatch_service / capability_pack —— import `AgentContextService`、`build_agent_runtime_id`、`build_agent_session_id`、`build_private_memory_scope_ids`、`build_scope_aware_session_id`、`build_ambient_runtime_facts`、**`_dynamic_transcript_limit`（orchestrator.py:84 私有名跨模块 import）**
- 生产+测试 import dataclass：`SystemPromptContext` 等 ← task_service.py + test_task_service_context_integration.py
- 测试：test_task_service_context_integration / test_control_plane_api 等 import 7+ 个 `build_*` 函数
- **无 star import；无 patch/mock AgentContextService 方法；无 `from agent_context import _私有方法`** ✅

### 3.5 循环 import 风险（本次拆分的核心结构约束）
mixin 文件需要引用 dataclass（`SessionReplayProjection`/`SystemPromptContext` 等）与自由函数（`build_scope_aware_session_id` 等）。若它们留在 agent_context.py，则 mixin → agent_context → mixin 成环。
**解法（定稿）**：常量 + 4 个 dataclass + 全部 module-level 自由函数先移 `agent_context_helpers.py`（零依赖叶子文件），mixin 与主文件单向依赖 helpers；agent_context.py 显式 re-export 保持全部外部 import 路径不变。F093 的 turn_writer mixin 已验证此模式（它只依赖 core models，无回向依赖）。

## 4. 测试环境约束（防假 0 regression）

- worktree `octoagent/.venv` 是 **symlink → 主仓 .venv**，editable .pth 指向**主仓 src**。裸跑 pytest 会测 master 代码而非 worktree 改动
- 全部测试命令必须 `PYTHONPATH` 前置 worktree 的 `apps/gateway/src:packages/core/src:...`（memory `project_worktree_venv_symlink` 已记录此陷阱）
- 跑测试用 `uv run --no-sync python -m pytest`（memory `project_pytest_invocation_env_pollution`）

## 5. 影响面与风险评级

- 直接改动文件：1 拆 7（agent_context.py → 主文件 + helpers + 5 mixin）；外部文件 **0 改动**（re-export 保契约）
- 测试改动：**0**（所有直调路径经继承/re-export 保持可见）
- 影响文件数 < 10，无跨包接口变更、无 schema 变更、无 LLM 工具变更
- 风险评级：**medium**（纯结构移动、行为零变更，但文件巨大、移动量 ~3500 行，主要风险是搬运遗漏/截断——靠方法清单 diff 对账 + 全量回归兜底）
- 预估拆后主文件 ~1050 行（4600 → -77%）。任务期望 ~600 行不可达：`build_task_context` 单方法 556 行是审计决议"不可抽出"的组合根；进一步压缩须拆解该方法内部，属行为变更风险更高的另一类重构，超出 F113"按职责簇拆 mixin"范围
