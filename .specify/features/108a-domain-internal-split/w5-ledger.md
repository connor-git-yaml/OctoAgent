# W5 对账清单（capability_pack 拆分 5 mixin）

> AC-3 制品。双评审：Codex **PASS 0H/0M/1L** + **Opus 席位中断（订阅月度用量上限，57 tool-use 后无产出）→ 主 session 按席位维度接管**（F103/F103c 先例：review 席中断 → 主 session 按维度手动验证）。

## 搬运对账

- capability_pack.py **2174→1523**（+18/−669）；5 新 mixin：browser 167（9 方法 + `_ssrf_request_hook` 单一定义）/ web 153（5 方法）/ media 111（4 方法）/ worker_plan 241（5 方法 + 2 dataclass + `_WORK_TERMINAL_VALUES`）/ availability 148（3 方法 + F086 墓碑注释随族走）。
- 方法级 **83/83 零豁免**（agent + Codex AST 一一对应 + 主 session 接管复跑三方一致）；顶层符号 11→16（豁免=类自身 + TYPE_CHECKING 块行号命名漂移，人工字节比对 BYTE-EQUAL）。

## 关键工程发现（新红线类别，进 F108b/后续 wave 红线库）

**模块级 patch 命名空间耦合**：方法搬运即使满足 `__get__`/getsource/MRO 三红线，仍可能破坏 `patch("<宿主模块>.<符号>")` 形态的测试——函数名字解析走**定义模块的 `__globals__`**。W5 实测两例：
1. `_launch_child_task` 留主文件（计划进 WorkerPlanMixin）：phase_d 7 用例 patch `capability_pack.get_current_execution_context`，迁出后 patch 永不生效（agent 第一轮实测 7 failed）。备选（复制 import 进 mixin 让测试 patch 两处 / 改测试 patch 路径）均需改测试 = 破 AC-2 红线 → **留守是唯一干净解**（主 session 接管复核机制判断正确：`__globals__['__name__'] == capability_pack` 实测确认）。mixin docstring 已写明防后人"补全"。
2. `import httpx` 0 引用锚点保留（带 3 行注释）：test_capability_pack_tools 2 用例 patch `capability_pack.httpx.AsyncClient`。

**Codex patch 路径全量审计：10 个命中全 SAFE，0 漏网**（含确认 async_ensure_url_safe/_truncate_text/structlog 无测试 patch；5 个 mixin 模块路径不在任何 patch 字符串中）。

## 豁免与决策记录

| # | 项 | 处置 |
|---|---|------|
| 1 | `_launch_child_task` + 连带 import（get_current_execution_context/structlog/_log/DelegationTargetKind/NormalizedMessage）留主文件 | 见上；主文件 1523 vs 预期 1100-1300 的 +223 全部来自此偏离 |
| 2 | `_ssrf_request_hook` 移 browser 模块 + **主文件 re-export 同一对象**（接管验证 `is` 确认） | `test_e2e_ssrf_guard.py:193` 直接 from capability_pack import |
| 3 | `_WorkerPlanAssignment`/`_WorkerPlanProposal`/`_WORK_TERMINAL_VALUES` 随 worker_plan 簇迁移 | W4 常量单一定义范式；主文件 0 残留引用（Codex 核） |
| 4 | 13 个删除 import AST 0 引用断言；13 个 pre-existing 死 import 留置 | W3/W4 同款裁定，归收尾 cleanup |
| 5 | Codex LOW：删除 import 计数 16 vs 13 | agent 报告按"名字"计、Codex 按 AST 对比计，无实质差异 |

## 验证

- wave 回归门：**4104 passed + 1 failed（test_sc3_projection，F083 已知 timing-race 家族——隔离复跑 ×4 全过 1.5s/次；本轮全量与双评审 agent 同机抢 CPU 触发）= 等效 4105 持平基线** / 3:18
- 焦点 6 文件：68 passed + 1 xpassed（既有过时 xfail），**HEAD baseline stash 对照跑完全持平**；加跑 import 方 20 passed
- 描述符 smoke：`__get__` 重绑 / getsource（真实断言：仅匹配调用语法，1081 行 F098 墓碑注释被显式容忍）/ staticmethod+classmethod 类级直调 / 12 staticmethod descriptor 全核
- e2e_smoke：commit hook 自动

## 双评审 finding 闭环表

| Finding | 处置 |
|---------|------|
| Codex PASS（P1 patch 审计 0 漏网 / P2-P6 全过）+ 1 LOW 计数差异 | 豁免 #5 |
| Opus 席位：**中断未出席**（spend limit）| 主 session 接管六维：①对账复跑 83/83 ②__globals__ 机制实证 ③httpx 锚点备选评估（改测试=破红线，留守正确）④锚点测试（agent 68+1xp + HEAD 对照持平 + 全量门）⑤描述符三红线真实断言复测 ⑥AC-1~6 对照——全过。**记 limitation：W5 第二席为主 session 接管而非独立 Opus 模型**（F103c 先例） |

**0 HIGH 残留。**
