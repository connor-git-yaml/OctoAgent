# F108b Cross-Layer Contract — Completion Report（含 F108 program 总收口）

> 基线：master ecb1f6ce（F108a 合入后）。分支 `feature/108b-cross-layer-contract`（**未 push，等用户拍板**）。

## 实际 vs 计划（program plan §W6-W8 v2）

| Wave | 计划 | 实际 | 偏离 |
|------|------|------|------|
| W6 | cache 类下沉 tooling + living-docs 三层定调 | ✅ 48 行类字节级下沉（sha256 双侧一致）+ 注解诚实化 Protocol + §2.8 职责表格/4 跨层契约/设计原则 + module-design 3 处 | re-export 兜底经实证无 import 面省略（w6-ledger #1） |
| W7 | typed registry + _get_service 9 处 + 7 处跨 service 赋值 + bind_* 保留 | ✅ ControlPlaneServiceRegistry（构造期 TypeError 前移）+ 3 concrete accessor（错误语义字节锁 ×3 测试）+ killing test | **fail-fast accessor 刻意不做**（消费方 None-check 是 Constitution #6 故意降级，plan 字面与 impact-report 不一致，实施保语义优先——w7-ledger #1，双席确认正确） |
| W8 | C1 LOW 闭环 + C2 AmbientRuntime（行为变更独立 commit）+ C3 制品 | ✅ C1 四项（2 实施 + 2 评估零动作）；**C2 用户拍板四要件全落实（含 revert 实测）**；C3 本制品链 | scan_context docstring 项实测 F125 已修（计划信息滞后） |

## AmbientRuntime 行为变更单列（用户拍板要件）

**F108 全程（a+b 共 24 commits）唯一显式行为变更 = commit `9839927a`**：AmbientRuntime 块自 Block 1 冻结前缀中段移到 Block 2 尾部（内容字节不变）。动机：秒级时间戳让整个 system 前缀每秒缓存失效。影响：LLM 见同样信息靠后位置；Block 1 + Block 2 前段从此可缓存。可单独 bisect/revert（双席实测）。残留核查项：cap_pack bootstrap 模板占位符路径（w8-ledger O6）。

## 验证总账

- 每 wave 全量门：W6 **4130** / W7 **4134**（+4 registry 测试）/ W8 **4134 + 1 已知 flaky（隔离 ×3 过）**——0 真回归。
- e2e_smoke 8/8 × 每 commit hook（F108b 共 8 commits）。
- 双评审：W6 Codex PASS 0 finding + Opus APPROVE 0H；W7 Codex CONDITIONAL 1L（killing test 已补）+ Opus APPROVE 0H0M2L；W8 Codex CONDITIONAL 0H0M2L（已闭环）+ Opus CONDITIONAL ACCEPT（阻断项=C3 本身）。**全程 0 HIGH 残留。**

## F108 program 总收口

- **F108a**（W1-W5 域内拆分，已合 master 4ecc74c2）+ **F108b**（W6-W8 跨层收口，本分支）= program plan 8 waves 全部完成。
- 架构债闭环：**D9**（cap_pack 超载拆解 + 三层职责文档定调）/ **F121**（6 巨型 service 拆分）/ **F118 D8**（typed DI 断链前移构造期）/ **D11**（WorkerRuntimeAdapter）/ **D12**（写核 two-phase 收口）——M6 表中 F108 行全部范围兑现。
- 设计输入处置：AmbientRuntime ✅ 折入；schema 校验 / tool_call_id eviction / artifact read-back **spin out**（用户拍板，待立项）；Manus 稳定排序 / az-1 扩展缝 → 设计原则文档化（§2.8）。

## 已知 limitations / 后续项

1. policy.py 私有 `_CONTENT_SCAN_SERVICE` 与新 default 单例语义重复，可收敛（w8-ledger O3，非阻断）。
2. cap_pack bootstrap 模板秒级占位符的 prefix-cache 完整性核查（w8-ledger O6）。
3. capability_pack 主文件可再压 ~130 行（`_launch_child_task`），需改 phase_d 测试 patch 路径（F108a w5-ledger）。
4. spin out 三项设计输入待立项（M6/M7 backlog）。
5. test_sc3_projection 偶发 race（F083 工程债家族，非 F108 引入）。
