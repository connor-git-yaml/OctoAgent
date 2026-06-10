# F125 修复任务清单（fix 模式）

> 基线 master 167b9cf4 | 上游 fix-report.md + plan.md
> 实施策略：**主编排器亲自实施**（安全敏感 + 正则需实测迭代 + 决策集中原则），不委派 implement 子代理；Codex adversarial review 把关。

## AC↔test 显式绑定（SDD 强化）

| AC | 验收点 | 绑定 test |
|----|--------|-----------|
| AC-1 | 热路径卸载：CONTEXT 扫描在线程跑，event loop 不阻塞 | `test_finalize_result_offload.py::test_scan_runs_in_thread` / `::test_eventloop_not_blocked_by_large_scan` |
| AC-2 | 误报收紧：≥30 真实语料负样本 0 命中 | `test_tool_result_threat_scan_false_positive.py::test_negative_samples_not_flagged` |
| AC-3 | 检出力保持：每条收紧 pattern 正样本仍命中 | `..._false_positive.py::test_positive_samples_detected` + `::test_per_pattern_detection` |
| AC-4 | MEMORY 零回归：PI-004 scope 回收后 MEMORY 不变 | `test_threat_scanner.py`（既有 L171-185）+ `test_threat_approval_integration.py`（既有 L201/244）+ `..._false_positive.py::test_memory_scope_unchanged_by_pi004_recall` |
| AC-5 | 语义不变量：never block / fail-open / 不改 raw / emit 留 loop | `test_tool_result_threat_scan.py`（既有全过）+ `test_finalize_result_offload.py::test_*_failopen` |
| AC-6 | docstring 单遍全文 | 人工核对 `threat_scanner.py` scan_context + `protocols.py` scan_tool_context |

## 任务

- [ ] T1 docstring 对齐单遍全文（threat_scanner.py scan_context + protocols.py scan_tool_context）— LOW
- [ ] T2 PI-004 scope 回收 `_MEM_CTX`→默认 MEMORY-only（正则不改；MEMORY 字节级不变）— MED
- [ ] T3 7 条 CTX pattern 收紧（CTX-RH-001 / C2-001 / C2-002 / C2-004 / HID-001 / DEC-001 / LEAK-001），全有界量词 ReDoS-safe — MED
- [ ] T4 诊断脚本实测迭代正则：现有 CONTEXT 正样本必须仍命中；18+ 真实语料 0 命中
- [ ] T5 false_positive 测试扩展：≥30 真实负样本（9 组）+ per-pattern 正样本 + MEMORY 零回归断言；阈值锁 0%
- [ ] T6 broker `_finalize_result` CPU 块抽模块级 `_scan_collect_findings` + `await asyncio.to_thread`；5 语义不变量保持 — HIGH
- [ ] T7 新增 `test_finalize_result_offload.py`（单元 patch to_thread + 集成心跳 <100ms + fail-open）
- [ ] T8 全量回归 0 regression vs 167b9cf4 + e2e_smoke 必过（不 SKIP_E2E）
- [ ] T9 Codex adversarial review 闭环 0 HIGH → completion-report

## 现有测试硬约束（收紧不得破坏）

- **必须仍命中 CONTEXT**：`register as a node ... beacon ... c2`（test_threat_scanner:197 / test_tool_result_threat_scan:308）、`you must register and beacon`（test_threat_scanner:235，靠 CTX-C2-003 不改）、`ignore all previous instructions`（PI-001 双 scope，不改）
- **必须仍不命中**：`this blog explains how HTTP caching works`（test_threat_scanner:201）
- **MEMORY 零回归**：`you are now a new persona` WARN（test_threat_approval_integration:201/244）；CTX-* 不入 MEMORY（test_threat_scanner:178/185）

## 实施顺序

T1 → T2 → T3 → T4（迭代到全绿）→ T5 → T6 → T7 → T8 → T9
