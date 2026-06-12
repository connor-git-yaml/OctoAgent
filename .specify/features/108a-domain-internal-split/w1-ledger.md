# W1 对账清单（behavior 域收口）— 字节级对账 + 豁免记录

> AC-3 制品。基线 = d6148903 + 计划制品 commit。双评审：Codex（1 MED + 2 LOW，0 HIGH）+ Opus（APPROVE，0 HIGH/0 MED 阻断）全闭环。

## C1 拆包对账

- **fidelity 工具**：`tools/check_move_fidelity.py`，`--original git:HEAD:...behavior_workspace.py --moved behavior_workspace/`
  - 原文件 80 顶层符号：**0 MISSING / 0 DIFF / 0 DUP**（`log` 双份豁免，两份均与原字节一致）
  - 3 个 `[NEW]` 全部为 C2 设计新增（`PendingBehaviorWrite` / `prepare_behavior_file_write` / `commit_behavior_file_write`）
  - `__init__` 通过"仅 import/docstring"检查
- **@cache 唯一性**：`_load_behavior_template_text` 仅 `template.py` 定义一次；`__init__` re-export 同一对象（运行时 `is` 验证 True，`cache_info` 在）
- **映射偏离 2 处（破循环 leaf 移动，映射条款允许）**：
  1. `_template_scope_for_file`（原 807-812）→ `paths.py`（按映射放 resolver 会成 paths→resolver→paths 环；实际调用方是 paths/skeleton）
  2. `_read_behavior_file`（原 777-778）→ `resolver.py`（唯一调用方 resolve_behavior_workspace；按映射放 skeleton 会成 resolver→skeleton→resolver 环）
- **依赖 DAG**：`_types`/`onboarding_state`/`validate`（叶）← `budget`/`paths`/`template` ← `resolver` ← `skeleton`；`write`（C2）← `budget`+`paths`。无环（import smoke 验证）。

## 豁免记录（唯一豁免逐条）

| # | 豁免项 | 理由 / 影响 | 来源 |
|---|--------|------------|------|
| 1 | import 行 / 模块 docstring 不参与对账 | 拆分结构性必然 | 工具内置 |
| 2 | `log = structlog.get_logger(__name__)` 双份（onboarding_state.py:13 / skeleton.py:36） | 源码字节相同；**运行期 logger-name 维度从 `octoagent.core.behavior_workspace` 变为子模块名**。全仓无测试 pin logger name、无基于 logger name 的 routing/processor。F093/F113 同类先例已接受 | Codex F2 = Opus O1（LOW，双席同向） |
| 3 | **顶层 import 别名不再透传**（原文件 24-37 顶层 `from .models...` import 的 `AgentProfile` + behavior 模型别名，原可经 `behavior_workspace.X` 属性访问） | AST 全仓扫描：52 个从 behavior_workspace import 的名字**全部被 package 覆盖，模型别名 0 真实消费方**（正主在 `octoagent.core.models`）。恢复透传 = 保留 incidental namespace 污染，不做 | Codex F1（MED）处置选 (b) 记账排除 |
| 4 | 4 个 section banner 注释随其描述符号归位（±3 行出入） | 注释非行为；AST 对账不含 | C1 报告 |

## C2 写核收口对账

- **契约形态**：计划写"单函数 `write_behavior_file_content`"，实现为**两段式** `prepare_behavior_file_write`（resolve+budget，不触盘）+ `commit_behavior_file_write`（mkdir+非原子 write_text）。理由：misc_tools 的 REVIEW_REQUIRED proposal 门**物理上夹在 budget 检查与写入之间**，单函数无法在不破坏门位置的前提下收口。prepare 提前算 budget 无可观测副作用（resolve/budget 均纯函数，Opus 独立验证）。**Opus O2 判定优于计划，已接受为正向偏离**。
- **时序等价**：resolve ValueError 先于 budget 检查（write.py:48-56 保持原序，Codex 核查通过）。
- **4 项 caller-specific 副作用全留原位字节未变**：事件发射（source 两处各异 control_plane/llm_tool）、proposal 门（misc_tools 258-269）、onboarding marker（294-308）、cache invalidation（310-315）。计划"248-324 不动"措辞澄清（Opus O3）：**该区间内除写核调用点（272-273 两行→一行 commit 调用）外零字节改动**。
- **read 路径有意不抽 helper**（Opus O5 确认合理）：`_handle_behavior_read_file` 与 HEAD 字节级一致零改动。read 无双入口重复债（misc_tools 侧无 behavior 专用 read tool），强行抽 helper 是无收益搬运。
- **misc_tools lazy import 同位**：原 198 行函数内 import 改为同位置 import 新符号；顶部删除 `check_behavior_file_budget`（全文件唯一引用点已被 prepare 吸收）。

## Golden 锚（14 测试，apps/gateway/tests/services/test_behavior_write_golden.py）

- A 段（control_plane action 经 execute_action 公共契约）：成功（envelope code/message/data/resource_refs + 磁盘）/ INVALID_FILE_ID / BUDGET_EXCEEDED（逐字错误消息）/ **OSError → FILE_WRITE_ERROR**（Codex F3 补）
- B 段（builtin tool 经 _CaptureBroker）：proposal 门不触盘 / confirmed 成功（全字段）/ INVALID_FILE_ID / 超预算逐字 reason / BOOTSTRAP marker → onboarding_completed / **OSError → rejected reason**（F3 补）/ **cache invalidation spy 调用一次**（F3 补）
- C 段（写核单元）：prepare 不触盘 + budget 字段 / invalid ValueError / commit mkdir+utf-8

## 验证结果

- 焦点：golden 14 + test_behavior_workspace 62 = **76 passed**（后者零修改）
- wave 回归门：**4102 passed / 0 failed**（= baseline 4091 + 11 golden v1；+3 F3 补强后焦点再验）/ 13 skipped / 1 xfailed / 1 xpassed / 6 deselected（环境性真实 LLM 名单，§3.6 账本）/ 4 分 10 秒
- e2e_smoke：commit hook 自动跑（C1/C2 各一次）

## 双评审 finding 闭环表

| Finding | severity | 处置 |
|---------|----------|------|
| Codex F1 顶层 import 别名收窄 | MED | 选 (b)：豁免记录 #3，0 真实消费方实证，不恢复透传 |
| Codex F2 / Opus O1 logger-name | LOW | 豁免记录 #2，F093 先例 |
| Codex F3 golden 缺 3 分支 | LOW | 已补 3 测试（OSError×2 + cache spy） |
| Opus O2 两段式偏离计划 | INFO | 接受为正向偏离；refactor-plan W1 C2 文字已同步 |
| Opus O3 计划行号措辞 | INFO | 本清单澄清 |
| Opus O5 read 不抽 helper | INFO | 有意偏离，理由如上 |

**0 HIGH 残留。Codex 判定"建议修复后合入"（F1 处置完毕）；Opus 判定 APPROVE。**
