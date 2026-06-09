# F124 工具结果威胁扫描 —— Completion Report

**Feature**: F124 context-scope tool-result scan
**Branch**: `claude/magical-bardeen-0c8b21`（worktree，**未 push**）
**Baseline**: d2936e0 / 543a93b（F124 起点 parent）
**Commits**: 5（543a93b..HEAD）
**Status**: ✅ 实现完整 + 全测试 + Codex review 全闭环（spec 4 轮 + plan 4 轮 + final 3 轮）；待用户拍板 master 合入

---

## 1. 交付内容（What）

把 F084 的 ThreatScanner 从"只接 memory/profile 写入"扩到 **gateway Agent 的 tool 结果管道**（web.fetch/search/MCP/terminal），堵间接 prompt injection 面。完整链路：

```
tool 结果含注入 → broker._finalize_result（全 8 退出分支，扫 output+error 截断前全文，不改 raw）
  → 挂 ToolSecurityFinding + emit TOOL_RESULT_THREAT_FLAGGED（hash 无原文）
  → 流到 ToolFeedbackMessage.security_findings
  → provider_model_client._append_feedback_to_history 经 render_tool_result_for_llm 前置 [security-warning]（LLM 当轮可见，不改 raw）
  → JSON-native 持久化进 AgentSessionTurn.metadata（扛 replay）
  → replay / memory-extraction / research-handoff 再入口从持久化 finding 重渲染
```

**核心不变量**：tool 结果**只标注不 block**（用户未撰写内容，避免误伤）；memory 写入仍 BLOCK（PolicyGate 不变）；raw output/error 全程不改写（机器消费者如 tool_search 读原值）；标注确定性（扛 replay + prefix-cache）。

## 2. Phase 实际 vs 计划

| Phase | 计划 | 实际 | 偏离 |
|-------|------|------|------|
| Foundational（T001-T013）| scope 维度 + ReDoS + chunk/cap/degraded + CONTEXT pattern + 类型 + DI + 测试 | ✅ 全做 | **chunk 后被 final review FR-F2 推翻**→改单遍全文扫描（见 §3）|
| Phase 3 C（T014-T019）| service + broker finalize 全分支 + 事件 + 测试 | ✅ 全做 | finalize 落点经 plan 2 轮 review 定为"两阶段→统一 finalize 全分支" |
| D1（T020-T022）| render helper + live 标注 | ✅ 全做 | helper 落点经 plan review 从 _build_tool_feedback 改 _append_feedback_to_history（真实物化点）|
| E1（T023-T024）| finding JSON-native 持久化 | ✅ 全做 | — |
| D2（T025-T028）| replay/compaction/memory-extraction/research-handoff 再入口 | ✅ 做 replay/memory-extraction/research-handoff | **compaction 经核实非 tool-result sink**（`_load_conversation_turns` 仅 USER_MESSAGE/MODEL_CALL_COMPLETED）→ 不 wire（实测偏离，合理）|
| Phase F（T032-T034）| 误报门槛 | ✅ 全做 | — |
| Phase G（T035-T039）| C10 + Blueprint + 回归 + Codex final + report | ✅ 全做 | C10 PolicyGate via service；Blueprint 同步 harness-and-context.md（blueprint/ 索引文档见 §5 limitation）|

## 3. Codex Review 闭环（spec 4 + plan 4 + final 3 = 11 轮）

| 阶段 | 轮次 | finding | 闭环 |
|------|------|---------|------|
| spec | 4 | 3H+3M / 3H+1M / 3H / 4H | 全闭环（§spec §11）|
| plan | 4 | 4H+1M / 2H+1M / 2H / 1H | 全闭环（§plan §11）|
| **final** | **3** | r1 2H+2M / r2 1H / r3 1H | **全闭环（见下）** |

**final review 闭环明细**：
- **r1（2H+2M）**：FR-F1 finalize 未扫 after-hook 改写后 final output → 改 raw+final 双扫去重；FR-F2 chunk overlap + 无界 `\s+` 跨块绕过 → **去 chunk 改单遍全文**（删死代码 _CHUNK_SIZE/_context_overlap）；FR-F3 research-handoff 直调 scanner 绕过 service → 改经 ContentThreatScanService + 删 user_profile 死 import + 强化 no-bypass 测试；FR-F4 degraded 仍全量 hash → 有界 hash（64KB prefix+length）。
- **r2（1H）**：research-handoff `error_summary` 字段渲染进 block 但漏扫 → 改扫**完整 block**。FR-F1/F2/F3/F4 r2 确认全 CLOSED。
- **r3（1H，范围裁决）**：`octoagent-sdk` 独立 agent loop 绕过 F124 → **用户拍板范围外**（独立库、零依赖 gateway、不被产品路径用、自带 policy；接入需 scanner 下沉，超 F124）。spec §2.2 + plan + no-bypass 测试显式排除 + 文档理由。

**教训实证**：CLAUDE.local.md"大 fix 后必 re-review、至少 2 轮收敛"在 final review 三轮印证——r1 修完 r2 抓 error_summary、r2 后 r3 抓 SDK 范围。

## 4. 测试 + 回归

- 新增：`test_tool_result_threat_scan.py`（broker 检测/分支/raw 不改/持久化/replay-survival/no-bypass/research-handoff/中央覆盖）+ `test_tool_result_threat_scan_false_positive.py`（9 负样本 0 误报 + 4 正样本全检出）+ `test_threat_scanner.py` 扩（scope/有界/no-circular-import，+13）。
- **broad 回归 2765 passed, 0 失败**（apps/gateway 除 e2e_live + tooling/skills/core）+ **e2e_smoke 8 passed**。0 regression vs baseline。
- MEMORY 路径字节级等价（FR-5.2 回归样本断言）。

## 5. 已知 limitations / living-docs drift

- **`octoagent-sdk` 范围外**（用户拍板）：独立 agent 运行面不在 F124 保护内。若 SDK 升为受支持产品面，需独立 Feature（scanner 下沉共享包 + SDK render 接入 + no-bypass 纳入）。
- **Blueprint 索引文档**：`docs/blueprint/`（core-design / module-design / architecture-audit）的 ThreatScanner/PolicyGate 提及未逐一同步（实现级 `docs/codebase-architecture/harness-and-context.md` 已同步 2.3/2.6）。建议 M6 顺手清或独立 doc Feature。
- **OctoBench scorer**：若写 Tier1 bench task 断言 tool 结果扫描，需把 `TOOL_RESULT_THREAT_FLAGGED` 加进 `DEFAULT_TIER1_EVENT_TYPES`（F114 先例）。本 Feature 不含 bench task。
- **max_span 字段**：去 chunk 后不参与扫描逻辑，保留为 CONTEXT pattern 有界性文档标记 + 未来窗口化预留。

## 6. master 合入建议

- 全部 acceptance gate 通过：实现完整 / 11 轮 Codex 闭环 0 HIGH 残留 / broad 2765 + e2e_smoke 8 passed 0 regression / Blueprint 同步 / SDK 范围带理由排除。
- 5 commit 在 feature 分支 `claude/magical-bardeen-0c8b21`，**未 push**。
- **建议合入 origin/master**（按 CLAUDE.local.md spawn-task 流程，等用户显式确认 push）。
- F124 落地后 **F108 应移除"tool 结果 context-scope scan"设计输入**（已由 F124 兑现）；F124 待**加入 CLAUDE.local.md M6 roadmap**（编号自分配，需用户确认）。
