# F108a — Domain-Internal Split（域内机械拆分，W1-W5）

> 上游：`.specify/features/108-capability-layer-refactor/`（program 级 impact-report + refactor-plan v2，双评审已闭环，用户 2026-06-12 拍板拆分）。
> 性质：**行为零变更结构重构**（F113 范式）。基线 origin/master `d6148903`。
> 姊妹 Feature：F108b Cross-Layer Contract（W6-W8，待 F108a 全部合入后启动）。

## 范围（= program plan W1-W5）

| Wave | 内容 | 文件 |
|------|------|------|
| W1 | behavior 域收口：behavior_workspace 拆 package + D12 写核收口 + worker_service behavior IO 回迁 | `packages/core/.../behavior_workspace.py` → package；`worker_service.py`；`misc_tools.py` |
| W2 | coordinator 瘦身（action registry 555 行抽出 + telegram 解析抽出）+ D11 `LLMWorkerAdapter`→`WorkerRuntimeAdapter` | `_coordinator.py`；`orchestrator.py` |
| W3 | setup_service 拆分（review 引擎 / config IO / skill selection 三 mixin + helpers） | `setup_service.py` |
| W4 | worker_service + session_service 拆分（helpers + revision mixin / projection helpers） | `worker_service.py`；`session_service.py` |
| W5 | capability_pack 拆分（Browser/WebSearch/MediaInspect/WorkerPlan/ToolAvailability 5 mixin） | `capability_pack.py` |

每 wave 明细、红线、对账边界以 program plan `refactor-plan.md` §2（v2，双评审修订版）为准——本 spec 不复制，避免双源漂移。

## 验收标准（AC）

- **AC-1（零变更总门）**：每 wave 后全量回归 vs baseline 账本 0 regression（`refactor-plan.md` §3.6：4091 passed 基准 + 6 个环境性 e2e_live 真实 LLM 失败按名单记账）+ e2e_smoke 8/8。
  - 绑定：全套现有测试（零修改通过即是验收）；账本对比记录进各 wave commit message。
- **AC-2（测试零修改）**：全部既有测试文件**零修改**通过；特别是私有直调锚点：`test_behavior_workspace.py`（6 私有符号 import）、`test_capability_pack_phase_d.py:75`（`__get__` 重绑）、`test_phase_c_worker_to_worker.py:37`（`inspect.getsource`）、`test_capability_pack_web_search.py`（类级 staticmethod 直调）、`test_capability_pack_tools.py`、`test_graph_pipeline_security.py`。
- **AC-3（字节级对账）**：每 wave 产出对账清单（搬运块前后逐字节 diff，唯一豁免=import/class 头/缩进，逐条记录），W1 附加：`@cache` 单一定义 + `__init__` re-export 同对象；golden response 对账（behavior 写入两入口）。
- **AC-4（import 契约不变）**：`from octoagent.core.behavior_workspace import X` 全部既有 import 路径零变化（package `__init__` 全量 re-export 含 7 个私有符号）；lazy import 原位（setup 3+`_cp_pkg`、worker 1366、coordinator 11、session 2）。
- **AC-5（残留扫描）**：每 wave 旧符号/旧路径全仓 grep 零残留（豁免显式归档）。
- **AC-6（双评审）**：每 wave commit 前 Codex + Opus 双评审，0 HIGH 残留。

## 非目标

harness/broker 结构调整、跨层移动（W6）、typed DI（W7）、一切行为变更项（W8）→ F108b；schema 校验/tool_call_id eviction/artifact read-back → 独立 Feature（用户已拍板 spin out）。

## 完成定义

W1-W5 全部合入 master + completion-report（对照 program plan 标实际 vs 计划）+ handoff（交接 F108b）。
