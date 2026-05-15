# F100 Final Cross-Phase Adversarial Review

**Date**: 2026-05-15
**Reviewer**: Codex GPT-5.4（挑战者立场）
**Scope**: spec v0.3 + plan v0.3 + Phase C/F/D/E1/E2/G 全部 commit diff

---

## Findings（按 severity 排序）

### HIGH-1: patched runtime_context 未覆盖 stale `runtime_context_json`

- **位置**：`octoagent/apps/gateway/src/octoagent/gateway/services/orchestrator.py:_prepare_single_loop_request` 内（约 line 826-861）
- **问题描述**：
  - chat.py 在首次派发时把 pre-decision seed RuntimeControlContext（`delegation_mode="unspecified"`）写入 `dispatch_metadata[RUNTIME_CONTEXT_JSON_KEY]`
  - `_prepare_single_loop_request` 生成 patched runtime_context（`main_inline + skip`），但 `updated_metadata` 是 `**metadata` 浅拷贝—— **保留了 chat.py 写入的旧 `runtime_context_json` key**
  - 下游 LLMService.py:382 调 `runtime_context_from_metadata(metadata)` 读到的是**旧的 unspecified** 而非 patched 后的 main_inline
  - Phase E2 移除 fallback 后：`is_single_loop_main_active(unspecified_rc, metadata)` → False（应该 True）
- **影响**：
  - chat 主链 single_loop 路径行为漂移——main_inline 应该 skip recall planner，但 LLMService 读到 unspecified → 走 standard routing 路径
  - 这是 F051 性能优势的隐性回归！
  - 触发：所有走 single_loop main 路径的 chat 请求
- **建议**：在 `_prepare_single_loop_request` 末尾 model_copy 前，同步把 patched runtime_context 序列化覆盖 `updated_metadata["runtime_context_json"]`。验证补一个 chat-style 集成测试。

### HIGH-2: ask_back resume 的 AC-5 / FR-E 未闭环

- **位置**：`spec.md` AC-5（line 158-164）+ FR-E1/E2（line 358-370）
- **问题描述**：
  - spec AC-5 文字："runtime_context.recall_planner_mode 仍按 worker_inline 解析为 skip，turn N+1 不跑 recall planner"
  - 实际 Phase F 实测（phase-f-resume-trace.md §4）：runtime_context = None（TASK_SCOPED_CONTROL_KEYS 不含 runtime_context_json）→ helper return False → **跑 recall planner**（不 skip）
  - test_ask_back_recall_planner_resume_f100.py 单测显式断言 `is_recall_planner_skip(None, resume_metadata) is False`
  - **spec 与 code 行为矛盾**：spec 写"跑 skip"，code 实际"跑 full"
- **影响**：AC-5 不能闭环；F101 / 独立 Feature 若按 spec 文字预期 ask_back resume skip，会发现实际不是
- **建议**：
  - 二选一：（A）改 code 真实现 skip——需把 runtime_context_json 加入 TASK_SCOPED_CONTROL_KEYS 或显式 patch；（B）改 spec/AC 反映实际行为——"resume 后 runtime_context 信息丢失，turn N+1 跑 recall planner"
  - **推荐 B**（与 baseline 兼容，YAGNI；F100 不动 TASK_SCOPED_CONTROL_KEYS）+ deferred 到 F101 handoff
  - spec AC-5 / FR-E1/E3 文字重写

### MEDIUM-1: AC-PERF-1 的 ≤5% hard gate 未真执行

- **位置**：`spec.md` AC-PERF-1（line 242-252）+ phase-g-perf-report.md §4
- **问题描述**：
  - spec AC-PERF-1：F100 commit vs F099 baseline 回归 ≤ 5%
  - 实际 perf 测试只断言绝对值 < 100μs；没有跑 F099 baseline 对比数据
  - phase-g-perf-report.md §4 写"实测未恶化"是基于 F100 代码内部的相对路径对比，非真实 baseline 数据
- **影响**：理论上 perf gate 未真闭环——若 F100 引入 1.5x 减速，单测仍 PASS（绝对值仍 < 100μs）
- **建议**：
  - 实际：F100 的 helper 改动是单 if-check + switch 几行，性能影响极小（实测 0.05-0.08μs 远低于任何合理基线）
  - 缓解：AC-PERF-1 文字标注"基于绝对耗时阈值 + 相对路径分支对照"（v0.2 修订原意），删除"≤ 5% 回归"hard gate 描述
  - 或：将来若引入 perf regression 担忧，可建立长期 baseline 数据库（超 F100 范围）

### MEDIUM-2: `_with_delegation_mode()` 清掉 base context 的 `force_full_recall=True`

- **位置**：`octoagent/apps/gateway/src/octoagent/gateway/services/orchestrator.py:_with_delegation_mode`（约 line 905-910）
- **问题描述**：
  - 当上游通过 `request.runtime_context` 或 `metadata["runtime_context_json"]` 已经传入 `force_full_recall=True`，调用 `_with_delegation_mode(force_full_recall=None)` 时：
    - `resolved_force_full_recall` = `OrchestratorService._metadata_flag(metadata, "force_full_recall")` → 如果 metadata 不含此 key 则 False
    - patched runtime_context 写入 False，**覆盖了 base.force_full_recall=True**
  - F101 接 producer 时通过 runtime_context 字段传 force_full_recall=True 会被静默清掉
- **影响**：F101 接入 producer 后可能踩坑——通过 runtime_context 字段传入的 force_full_recall=True 被吞掉
- **建议**：修改 `resolved_force_full_recall` fallback 顺序：
  1. 显式 kwarg
  2. `metadata["force_full_recall"]` flag
  3. **`base.force_full_recall`**（保留上游已设的值）
  4. False（默认）

### LOW-1: spec v0.3 仍残留旧 "unspecified raise" 语义

- **位置**：spec.md US-6（line 138-143）/ FR-C3（line 333）/ FR-G2（line 397）/ NFR-3（line 416-419）
- **问题描述**：
  - spec v0.3 §0.3 已修订主体 AC-8/9 + FR-D1/D2 到 "unspecified → return False"
  - 但 US-6 / FR-C3 / FR-G2 / NFR-3 等处仍写 "fail-fast / raise ValueError" 描述
- **影响**：未来读 spec 的人会被混淆。当前 code 实际是 return False，spec 不一致
- **建议**：spec.md 全文 grep "raise" / "fail-fast" / "ValueError" 检查每处是否符合 v0.3，修订 US-6 / FR-C3 / FR-G2 / NFR-3 等残留描述

---

## 已验证无问题（review 通过）

- ✅ DelegationMode AUTO switch 覆盖全部 4 个显式取值（main_inline/worker_inline/main_delegate/subagent）；unspecified 走 v0.3 return False
- ✅ `force_full_recall` 在 helper 内先于 delegation_mode 判断（优先级正确）
- ✅ `_with_delegation_mode()` 有 production 调用链：`orchestrator._prepare_single_loop_request` line 849-854 + 780-785
- ✅ Phase E2 移除 fallback 后无遗漏 production reader 读 `metadata["single_loop_executor"]`
- ✅ `force_full_recall` 是 per-request 字段（写入 runtime_context.model_copy update），无全局共享线程安全问题

---

## 总评

- **finding 统计**：2 HIGH / 2 MEDIUM / 1 LOW
- **F100 是否准备好合入 origin/master**：**NO**（HIGH-1 是隐性 baseline 行为漂移，必须修；HIGH-2 是 spec/code 一致性问题，必须 align）
- **关键修复优先级**：
  1. **HIGH-1**：`_prepare_single_loop_request` 覆盖 `runtime_context_json` — 真实施
  2. **HIGH-2**：spec AC-5 / FR-E 重写为反映实际行为 + F101 handoff 记录
  3. **MEDIUM-2**：`_with_delegation_mode` 保留 base.force_full_recall
  4. **MEDIUM-1**：spec AC-PERF-1 措辞修订
  5. **LOW-1**：spec v0.3 残留 raise 描述清理
- **F101 handoff 完整性**：需补 §"AC-5 ask_back resume baseline 行为承接"

---

**Status**: Final review 完成，需进入 fix 阶段闭环 2 HIGH + 2 MED + 1 LOW
