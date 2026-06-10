# F113 残留扫描报告（residual-report）

> 扫描时点：Batch 1-6 + getter import 还原 commit（3f2202a6）后。
> 方式：AST 结构对账（最强）+ 模块独立加载 + 全仓 import 枚举验证，全部确定性脚本，非人工目测。

## 1. 定义完整性对账（AST 逐节点）

- 拆前（167b9cf4 版 agent_context.py）共 **92 个模块级/类级定义**（61 类方法 + 20 自由函数 + 4 dataclass/类 + 7 常量）
- 拆后 7 文件收集对账：**零丢失**；92 个定义全部找到
- 等价性（最终以**字节级**为准，两轮收紧）：81 个函数/方法 + 10 个类/常量中 **90/91 byte-identical**；唯一豁免 `_render_snapshot`——其 f-string 内 `AgentContextService._render_list` 类名自引用改为 `AgentContextPromptAssemblyMixin._render_list`（同一 staticmethod 函数对象，调用语义等价；mixin 文件不可回向 import 主类）

### 扫描抓出并已修复的真问题

**Batch3 的 ruff `I001 --fix` 动了 5 个 getter 函数体内的 lazy import**：4 个（get_consolidation_service / get_derived_extraction_service / get_tom_extraction_service / get_profile_generator_service）被重排顺序——函数内 import 执行顺序属行为面（模块首次加载副作用顺序），违反零变更标准，按 baseline 原文整体还原（commit 3f2202a6）；第 5 个 get_reranker_service 仅被单行→多行括号换行（AST 等价、零行为），AST 级对账未报、由第二评审（Opus 字节级 diff）抓出，同样按 baseline 原文还原。
教训已沉淀：①对搬运文件只可 `--select F401 --fix`（删未使用 import），不可叠加 `I001`（会动函数体内 import 块）；②零变更重构的对账标准应直接用字节级（AST 级会放过格式 diff，导致"唯一豁免"声明不诚实）。

## 2. 循环 import

7 个模块（helpers + 5 新 mixin + 主文件）独立 `importlib.import_module` 全部干净通过。依赖方向实测：`helpers ← {mixin, 主文件}`、`mixin ← 主文件`，无环 ✅

## 3. 全仓外部 import 验证

- 枚举全仓全部 `from ...gateway.services.agent_context import X` 语句共 38 个不同名字，逐一 `hasattr(agent_context, X)` 验证：**全部可解析** ✅（re-export 兜底生效，外部文件零改动）
- 扫描中出现的 4 个"缺失"（AgentRuntimeStatus / ContextSourceRef / WorkerProfileOriginKind / WorkerProfileRevision）为 regex 误匹配**另一个同名模块** `packages/core/src/octoagent/core/models/agent_context.py`（`from ..agent_context import` 相对引用），与本次拆分无关，已逐条确认假阳性
- 类名直调静态方法 4 处（`_build_memory_scope_entries` 跨包测试 / `_memory_hit_payload` task_service 生产代码 / `_build_ephemeral_subagent_profile` / `_build_research_handoff_block`）经 MRO 实测 callable ✅
- `_shared_llm_service` / `_shared_provider_router` / `_shared_background_tasks` 3 个类属性留在主类，e2e reset 路径不变 ✅

## 4. 行数与 lint 状态

| 文件 | 行数 |
|------|------|
| agent_context.py（主：编排根 + re-export） | 1079（拆前 4600，**-76.5%**） |
| agent_context_entity_ensure.py | 1149 |
| agent_context_prompt_assembly.py | 862 |
| agent_context_session_replay.py | 705 |
| agent_context_memory_recall.py | 547 |
| agent_context_helpers.py | 481 |
| agent_context_memory_services.py | 239 |
| agent_context_turn_writer.py（F093 不动） | 216 |
| **合计** | **5278**（+678 = 7 份文件头 import/docstring 开销，内容零丢失见 §1） |

- ruff `F821 / E402 / F401`（新引入维度）：全部干净；主文件保留与 baseline 完全一致的 9 个预存 F401（不顺手清理，超 F113 范围）
- 每批 focused 测试组（services 全目录 + 4 个直调测试文件 + 跨包 store 测试）：377 passed × 5 轮；唯一一次 test_f101 ask_back 偶发 fail 经单跑/整文件/重套件 3 次复验全过（F083 known flaky 同类，与本改动无关）

## 5. 残留数

**0**——无遗漏方法、无循环 import、无外部 import 断链、无 AST 级行为漂移（唯一豁免已记录）。
