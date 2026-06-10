# F113 分批重构规划（refactor-plan）

> 上游：impact-report.md（实测数据）。原则：**行为零变更**——方法签名 / `self.X` 引用 / 实例化方式 / 外部 import 路径全部不变；纯结构移动，不顺手改任何逻辑。

## 目标结构（1 拆 7）

```
services/
├── agent_context.py                    # 主文件 ~1050 行：import + re-export + AgentContextService(编排根 905)
├── agent_context_helpers.py            # 新：常量 3 + SystemPromptContext + 3 dataclass + 20 自由函数 ≈ 470 行
├── agent_context_entity_ensure.py      # 新：AgentContextEntityEnsureMixin（14 方法 ≈ 1049 行）
├── agent_context_memory_services.py    # 新：AgentContextMemoryServiceMixin（8 方法 ≈ 192 行）
├── agent_context_memory_recall.py      # 新：AgentContextMemoryRecallMixin（7 方法 ≈ 476 行）
├── agent_context_session_replay.py     # 新：AgentContextSessionReplayMixin（12 方法 ≈ 644 行）
├── agent_context_prompt_assembly.py    # 新：AgentContextPromptAssemblyMixin（12 方法 ≈ 773 行）
└── agent_context_turn_writer.py        # F093 已有，不动
```

类声明（方法名全唯一，MRO 顺序无行为影响）：

```python
class AgentContextService(
    AgentContextPromptAssemblyMixin,
    AgentContextSessionReplayMixin,
    AgentContextMemoryRecallMixin,
    AgentContextMemoryServiceMixin,
    AgentContextEntityEnsureMixin,
    AgentContextTurnWriterMixin,
):
```

> 实施记录：实际声明顺序如上（逐批插入产生，与本节初稿示意相反）。6 mixin + 基类**零方法名碰撞**已实测（第二评审复核），MRO 顺序对解析无影响。

依赖方向（无环）：`helpers ← {mixin 文件, agent_context.py}`；`mixin 文件 ← agent_context.py`。mixin 之间运行时经 `self` 互调、文件层面互不 import。

## 关键决策

1. **helpers 先行（Batch 1）**：常量/dataclass/自由函数移到零依赖叶子文件是打破"mixin ↔ 主文件"循环 import 的前提，不只是减行数。
2. **re-export 保契约**：agent_context.py 对全部移出的 module-level 名字（含 `_dynamic_transcript_limit` 被 orchestrator.py:84 跨模块 import 的私有名）做显式 re-export；6 个生产文件 + 4+ 测试文件的 import 路径零改动。ruff F401 处理：主文件继续使用的名字天然豁免；纯 re-export 名字用 redundant-alias（`import X as X`）或 `# noqa: F401`，以 `ruff check` 实测为准。
3. **沿用 F093 范式**：mixin 无状态、类级 annotation 声明依赖属性（`_stores: Any` 等）、docstring 写"依赖约定（由继承类提供）"+ 职责边界声明（防后续 Feature 堆回，对应任务要求的边界注释）。
4. **`_shared_*` 3 个类属性 + 3 个 `set_*` classmethod 留主类**（e2e 测试直接 get/setattr `AgentContextService.<attr>`）。
5. **编排根不拆**：`build_task_context`(556) / `build_recall_planning_context`(46) / `_build_context_request`(87) / `_resolve_context_bundle`(154) 留基类——审计 A4 决议，跨 4 簇组合根。主文件 ~1050 行而非任务期望 ~600：差额即 build_task_context 单方法 556 行，拆它属于"方法内部分解"另一类重构，超 F113 范围（已在 impact-report §5 说明）。
6. **mixin 文件 import 头生成法**：先复制主文件完整 import 区 → `ruff check --select F401 --fix` 确定性删除未使用项（避免人工漏配）。

## 批次执行表（每批后：import 冒烟 + ruff + focused 测试）

| 批 | 内容 | 移动量 | 验证 |
|----|------|--------|------|
| **B1** | 建 agent_context_helpers.py（常量+dataclass+自由函数）+ 主文件 re-export | ~470 行 | 冒烟 + ruff + focused 测试组 |
| **B2** | AgentContextEntityEnsureMixin（14 方法，最大最独立，审计"优先抽"） | ~1049 行 | 同上 |
| **B3** | AgentContextMemoryServiceMixin（8 方法，小批验证范式 + 被 B4/B5 依赖方法就位） | ~192 行 | 同上 |
| **B4** | AgentContextMemoryRecallMixin(7 方法) | ~476 行 | 同上 |
| **B5** | AgentContextSessionReplayMixin(12 方法) | ~644 行 | 同上 |
| **B6** | AgentContextPromptAssemblyMixin(12 方法) | ~773 行 | 同上 + 方法清单全量对账 |

focused 测试组（每批必跑，PYTHONPATH 锁 worktree）：
- `apps/gateway/tests/services/test_agent_context*.py`（phase_b/phase_c 等直调簇方法）
- `apps/gateway/tests/test_worker_session_turn_isolation.py`（replay 实例直调）
- `apps/gateway/tests/test_task_service_context_integration.py`（dataclass + build_* import + 静态直调）
- `packages/core/tests/test_agent_context_store.py`（跨包类名直调 `_build_memory_scope_entries`）
- `apps/gateway/tests/test_context_compaction.py`（局部 import AgentContextService）

实施手法：python 脚本按 impact-report 的方法行区间整段剪切（4600 行文件手工 Edit 易错），方法体字节级原样搬运；每批 commit 一次（回滚单元 = 批）。

## 残留扫描标准（Phase 4）

1. 方法清单对账：拆前 61 方法名集合 == 拆后 `AgentContextService` dir() 可见方法集合（继承链拍平）
2. 行数对账：7 文件总行数 ≈ 4600 + 新增文件头开销（每文件 import/docstring ~30-60 行），无大段丢失
3. `git diff` 内容审计：除 import/类声明/文件归属外零语义 diff（方法体逐字节一致）
4. 循环 import：`python -c "import octoagent.gateway.services.agent_context"` 干净通过
5. 全仓 grep：无外部文件需要改 import（re-export 兜底验证）

## 最终验证标准（Phase 5）

- 全量回归 0 regression vs baseline（167b9cf4 实测数字，后台已在跑）
- e2e_smoke 全过（pre-commit hook 同款）
- Codex adversarial review（重大架构变更节点，background）+ 第二模型 spec-对齐 review（SDD 多评审 panel 新规），分歧项列"必须人裁"，0 HIGH 残留
- living-docs 漂移闸：比对 `docs/codebase-architecture/harness-and-context.md` + `docs/blueprint/module-design.md` 中 agent_context 描述，drift 写入 completion-report
- completion-report.md + 归总报告；**不主动 push，等用户拍板**
