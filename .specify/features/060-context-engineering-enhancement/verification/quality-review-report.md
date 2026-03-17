# Feature 060 Context Engineering Enhancement -- 代码质量审查报告

**审查时间**: 2026-03-17
**审查范围**: Feature 060 变更文件（10 个文件：5 个源码 + 2 个新模块 + 3 个测试）
**参考文档**: plan.md / spec.md / constitution.md

---

## 四维度评估

| 维度 | 评级 | 关键发现 |
|------|------|---------|
| 设计模式合理性 | GOOD | 架构符合 plan.md 设计；模块职责清晰；BudgetPlanner 作为上游协调者独立模块正确；存在一处 `locals()` 反模式需修复 |
| 安全性 | EXCELLENT | 无硬编码密钥；无注入风险；进度笔记 Artifact 数据经 JSON 序列化安全处理；无 OWASP Top 10 风险 |
| 性能 | GOOD | 异步模式正确使用 per-session Lock；tiktoken 一次性初始化开销合理；存在一处 `_maybe_merge_old_notes` 中的 N+1 模式和后台 Task 泄漏风险 |
| 可维护性 | GOOD | 命名规范统一（英文标识符 + 中文注释）；v1/v2 迁移兼容路径清晰；少量函数偏长；有一处 bare except 和一处未使用变量 |

---

## 问题清单

| 严重程度 | 维度 | 位置 | 描述 | 修复建议 |
|---------|------|------|------|---------|
| WARNING | 设计模式 | `context_budget.py:204-211` | `locals()` 赋值反模式：代码试图通过 `locals()[_attr_name] = ...` 修改局部变量，但 Python 中 `locals()` 赋值对局部变量无效。注释 L212 已承认此问题（"locals() 赋值对局部变量无效"），但该分支的安全兜底逻辑（L203-211）实际上是死代码——循环内的 `locals()[_attr_name]` 赋值不会改变 `progress_notes_budget` 等局部变量的值。 | 将 L203-211 的循环替换为显式的变量赋值序列（类似 L150-192 的缩减逻辑），或直接删除这段死代码（因为 L214-217 的 `conversation_budget` 重算已经是真正的兜底）。当前代码虽然靠 L214 的重算偶然正确工作，但逻辑上存在误导。 |
| WARNING | 性能 | `progress_note.py:136-142` | `execute_progress_note` 在每次写入笔记后都调用 `_maybe_merge_old_notes`，而 `_maybe_merge_old_notes` 会执行 `list_artifacts_for_task` 全量加载。当笔记数量增长到接近阈值时，每次写入都做一次全量扫描是不必要的开销。 | 方案一：在 `execute_progress_note` 中添加简单的计数器或概率采样，仅每 N 次（如每 10 次）或随机 10% 概率检查合并。方案二：让调用方在 session 级别维护笔记计数，只在计数接近阈值时传入 `check_merge=True`。 |
| WARNING | 性能 | `context_compaction.py:196-197` | `_compaction_locks` 和 `_pending_compactions` 作为实例级 dict，没有清理机制。虽然 `_pending_compactions` 在 `_bg_compact` 的 `finally` 中会清理，但 `_compaction_locks` 的 Lock 对象会永久累积。在长时间运行的服务中，已结束的 session 的 Lock 不会被回收。 | 方案一：在 `_bg_compact` 的 `finally` 中同时清理 `_compaction_locks`（Lock 已释放且无引用时安全删除）。方案二：添加一个 `_cleanup_stale_locks` 方法，按需或定期清理。考虑到单用户系统活跃 session 数量有限，这是低概率但值得关注的长期问题。 |
| WARNING | 可维护性 | `context_compaction.py:191-194` | `_last_call_alias` / `_last_call_fallback_used` / `_last_call_fallback_chain` 作为实例级可变状态，在多次并发调用 `_call_summarizer` 时存在竞态条件。虽然当前单用户系统下极少触发，但设计上违反了状态管理最佳实践。 | 改为让 `_call_summarizer` 通过返回值传递 fallback 状态（如返回 tuple `(summary_text, alias_used, fallback_used, chain)`），由调用方组装到 `CompiledTaskContext` 中，而非依赖实例级可变状态。 |
| WARNING | 可维护性 | `progress_note.py:145-147` | bare `except Exception` 吞没所有异常，只返回 `persisted=False`。虽然符合 Constitution #6（Degrade Gracefully）的精神，但缺少任何日志记录，使得调试困难。 | 在 `except Exception` 块中添加 structlog warning 日志，记录异常类型和简要信息，如 `log.warning("progress_note_write_failed", error_type=type(exc).__name__, task_id=task_id)`。同样适用于 `_maybe_merge_old_notes` 的 L279 和 `load_recent_progress_notes` 的 L190。 |
| INFO | 可维护性 | `context_budget.py:15-18` | `context_budget.py` 从 `context_compaction` 导入私有函数 `_estimation_method`（前缀 `_` 表示内部实现）。跨模块导入私有符号违反封装原则，且如果 `_estimation_method` 被重命名或删除会导致 import 错误。 | 将 `_estimation_method` 重命名为 `estimation_method`（去掉前缀 `_`），或在 `context_compaction.py` 中添加公开的包装函数。当前 `_chars_per_token_ratio` 在测试中也被直接导入，建议统一为公开 API。 |
| INFO | 可维护性 | `context_compaction.py:192-194` | Fallback 状态字段（`_last_call_alias` 等）没有类型注解中的 `ClassVar` 标记，容易与 `_compaction_locks` / `_pending_compactions`（也是实例属性但语义为类级共享状态的 dict）混淆。 | 为实例级追踪状态添加注释区分其语义，或考虑用 `@dataclass` 封装。 |
| INFO | 可维护性 | `agent_context.py:4003-4023` | `_build_system_blocks` 中的 Skill 截断逻辑通过 `skill_text.split("\n\n--- Loaded Skill: ")` 解析 LLMService 的输出格式，存在格式耦合。如果 `_build_loaded_skills_context` 的输出格式变更，此处会静默失效。 | 方案一：让 `_build_loaded_skills_context` 返回结构化数据（如 `list[tuple[name, content]]`），由 `_build_system_blocks` 负责格式化和截断。方案二：在当前方案下添加 defensive 检查，确保 split 后的片段格式正确。 |
| INFO | 测试 | `test_context_budget.py:31-38` | `test_pure_english_reasonable` 测试断言过于宽松（`assert tokens > 0`），没有验证估算值与 len/4 的误差范围。对比 `test_pure_chinese_not_underestimated` 有明确的下界检查。 | 添加对英文场景的误差范围验证，如 `naive = len(text.strip()) // 4; assert 0.7 * naive <= tokens <= 1.5 * naive`。 |
| INFO | 测试 | `test_progress_note.py:53-91` | 多个 async 测试方法缺少 `@pytest.mark.asyncio` 装饰器。虽然 `pytest-asyncio` 可通过 `asyncio_mode = "auto"` 配置自动检测，但显式标记更清晰。 | 确认项目 pytest 配置是否启用 `asyncio_mode = "auto"`。如果是，当前写法可接受；如果不是，需要为所有 `async def test_*` 添加 `@pytest.mark.asyncio`。 |
| INFO | 可维护性 | `progress_note.py:93-94` / `progress_note.py:242-243` | `from ulid import ULID` 在函数内部延迟导入（两处），`from octoagent.core.models import Artifact, ArtifactPart, PartType` 也在函数内延迟导入。虽然可能是为避免循环导入，但 `ulid` 作为第三方库不存在循环导入风险。 | 将 `from ulid import ULID` 移到模块顶部，减少函数内重复导入开销。`octoagent.core.models` 的导入如果确实存在循环依赖，保持延迟导入并添加注释说明原因。 |

---

## 总体质量评级

**GOOD**

评级依据:
- CRITICAL: 0 个
- WARNING: 5 个
- INFO: 6 个
- 零 CRITICAL 问题，WARNING 数量 <= 5，代码质量良好

---

## 问题分级汇总

- **CRITICAL**: 0 个
- **WARNING**: 5 个
- **INFO**: 6 个

---

## 代码质量亮点

1. **架构设计与 plan.md 高度一致**: `ContextBudgetPlanner` 作为独立模块放在 `gateway/services/` 下，依赖方向正确（task_service -> budget -> compaction/agent_context），无循环依赖。模块依赖关系严格遵循 plan.md 的 Mermaid 图。

2. **三级 fallback 链实现精良**: `_call_summarizer()` 的 `compaction -> summarizer -> main` 链去重、逐级降级、全程日志追踪的实现质量高，完全对齐 Constitution #6（Degrade Gracefully）和 #13（失败必须可解释）。

3. **向后兼容设计完善**: `conversation_budget` 参数可选（`build_context()` 未传时回退 `max_input_tokens`）；`compaction_version` v1/v2 双版本读取兼容；`_parse_compaction_state` 静态方法干净处理版本差异。

4. **token 估算升级务实**: tiktoken 精确计算 + CJK 感知 fallback 的双层设计合理；`_chars_per_token_ratio` 抽取为独立函数供多处复用；模块级一次性初始化避免重复开销。

5. **Constitution 全面遵循**: 异步压缩结果通过 `rolling_summary` + `metadata["compressed_layers"]` 持久化（#1 Durability）；压缩事件保留完整审计（#2 Everything is an Event）；`progress_note` 工具声明 `side_effect_level: "none"`（#3 Tools are Contracts）；进度笔记 Artifact Store 不可用时优雅降级（#6 Degrade Gracefully）。

6. **测试覆盖关键路径**: `test_context_budget.py` 覆盖了正常分配、极端预算、缩减优先级、不变量检查等场景；`test_progress_note.py` 覆盖了写入/降级/加载/格式化/合并的完整生命周期；`test_context_compaction.py` 扩展了 fallback 链和三层压缩的回归测试。

7. **Skill 注入修复干净**: `llm_service.py` L314-315 的注释清晰记录了迁移原因，`_build_loaded_skills_context` 保留为公开方法供外部调用，避免了双重注入风险。`agent_context.py` 中的 Skill 截断逻辑按加载顺序保留且记录被截断的 Skill 名称，对齐了可观测性要求。

8. **进度笔记工具设计简洁**: `ProgressNoteInput` 使用 `Literal` 约束 status 值；`format_progress_notes_block` 与 `_build_system_blocks` 中的注入逻辑保持格式一致但各自独立（前者供测试/外部使用，后者供内部注入）。

---

## 改进建议（非阻断性）

1. **`context_budget.py` L203-211 的 `locals()` 反模式是本次审查中最值得优先修复的问题**。虽然 L214-217 的重算兜底使其在运行时不会产生错误结果，但死代码会误导未来维护者。建议在下一轮迭代中清理。

2. **考虑将 fallback 状态从实例可变属性改为返回值传递**。当前 `_last_call_alias` 等属性在并发场景下理论上不安全（即使当前单用户系统下概率极低）。这是一个设计清洁度问题，建议在代码稳定后统一重构。

3. **`progress_note.py` 中三处 bare `except Exception` 应添加日志**。符合 Constitution #13（失败必须可解释）和 #8（Observability）的要求，在调试生产问题时价值显著。

4. **`_compaction_locks` 的生命周期管理**应在后续版本中加入清理机制，防止长期运行的服务中 Lock 对象累积。可在 `_bg_compact` 的 `finally` 中简单清理：`if agent_session_id not in self._pending_compactions: self._compaction_locks.pop(agent_session_id, None)`。
