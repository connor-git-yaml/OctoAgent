# F106 User Plugin Loader — Spec Driver Trace

特性分支: feature/106-plugin-loader
Baseline: f3d8a267
模式: feature（完整编排）
Gate 配置: GATE_DESIGN=hard(always pause) / GATE_TASKS=always / GATE_VERIFY=always

---

## 执行链路

- [块 A] research/recon.md: 实测侦察完成（4 并行 Explore agent + 主节点综合 + Agent Zero helpers/plugins.py 深读）。核心判断：Model A 声明式 vs Model B 代码可执行。
- [Phase 2] specify: spec.md v0.1 完成（主节点亲写，Model A 推荐，3 决策点上 GATE_DESIGN）。
- [Phase 3] clarify + checklist: 并行完成。clarify 10 项（9 自决 + CL-7 NEEDS-HUMAN）；checklist 81/90 PASS + 9 GAP。
- [Phase 3 收口] spec v0.1 修订：闭 GAP-1（FR-4.5 fail-open）/GAP-2（FR-2.5 test）/GAP-3（FR-5.3 path traversal test）+ Constitution #4/#8 + 折入 clarify（PluginRejectedReason 枚举 / provenance / fallback-fill / 段7.5 / asyncio.Lock / PLUGIN_REMOVED / REST 状态码 / allowlist=KNOWLEDGE.md only）。
- [Phase 3.5] GATE_DESIGN（硬门禁）✅ 用户拍板：**Model B 代码可执行 + watchdog 热重载 + 内置 git**（全选 ambitious）。
- [Phase 3.5 后] spec v0.2 重写（Model B + §0.2 信任模型）。
- [spec adversarial review round-1] 双 panel（安全红队 + Constitution/arch，code-grounded）：9 HIGH + 9 MED 全闭环 → spec v0.3。纠正 2 FALSE reuse（scan_and_register 执行代码+忽略 registry / scan_memory API）+ honesty 重构（§0.3 residual：进程内无容纳）+ git 硬化 + race 闭合。记录 spec-review-r1.md。
- [Phase 4] plan: Phase A 详细 + B/C roadmap（review §13 分阶段：A declarative 安全切片 → B code+审批 → C watchdog+git）。
- [Phase 5] tasks: Phase A。
- [Phase 5.5/GATE_TASKS] 向用户呈现 plan + 分阶段 + Phase A 实施建议。
