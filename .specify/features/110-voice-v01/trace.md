# F110 语音 v0.1 — Spec Driver Feature 执行链路

- 模式：feature（动态编排，preset=quality-first）
- 基线：master HEAD `1cd2083f`（F109 STT only 已合入）
- 分支/worktree：`feature/110-voice-v01` / `.claude/worktrees/F110-voice-v01`
- research_mode：tech-only（产品调研跳过——单用户内部 Feature，scope 已由 handoff + 用户锁定）

## Phase 链路

- Phase 1b tech_research：COMPLETED | 产物 research/tech-research.md | TTS 选型（Piper/GPL-3.0）+ 4 块代码侦察 + voice session 落点 + 验证命令
  - 主节点 Perplexity 复核纠正：Piper 实为 **GPL-3.0**（非初稿误称 MIT），已修正 tech-research 许可证事实
- GATE_RESEARCH：AUTO_CONTINUE | policy=auto（非硬门禁）
- Phase 2 specify：COMPLETED | 产物 spec.md | 5 US / 28 FR / 24 AC（21 P1 带 test 绑定）/ MEDIUM
- Phase 3 clarify+checklist（并行 DESIGN_PREP_GROUP）：COMPLETED | clarify.md（1 CRITICAL: /voice off 后再触发语义）+ checklist.md（7/7 通过）
- **GATE_DESIGN：PAUSE（硬门禁 is_hard_gate=true）→ 用户拍板（2026-06-22）**
  - D1 = **Piper（接受 GPL-3.0）**
  - D2/D3 = **C 混合 + 显式关闭后不自动重开**（voice_mode 三态：unset/True/False；消解 clarify HIGH-1）
  - D4 = PyAV 优先（plan 实测 libopus）；D5 = 单 env 模型默认 zh_CN；scope = 异步多轮（实时双工 → v0.2）
  - 裁决已落 spec §2 + FR-D1/AC-D1/AC-D1b 细化
- Phase 4 plan：COMPLETED | plan.md（6 Phase + Phase 0 de-risk + read-modify-write 最大风险）
- Phase 5 tasks：COMPLETED | tasks.md（47 task / 26 P1 AC 全覆盖）
- GATE_TASKS：报告 + AUTO_CONTINUE（软门，用户偏好 one-session 仅硬门停）
- Phase 5.5 analyze：COMPLETED | 0 HIGH/MED，3 LOW（非阻塞）；FR 100%/P1 AC 100% traceability
- GATE_ANALYSIS：AUTO_CONTINUE（on_failure，无 failure）
- Phase 0（主节点 de-risk）：N_baseline=4341/1-F106（identical cmd）；venv 无 voice deps → hermetic Fake；PYTHONPATH 锁验证 F109 22 tests PASS
- Phase 6 implement：COMPLETED | 9 文件（4 新 5 改）；post-implement full 4365/1-F106（0 regression）；voice 38 tests
- **Phase 7 verify + 双评审 panel（Codex + Opus，新能力+外部GPL依赖强制）**：
  - 2 HIGH（H1 piper API 错用 / H2 AC↔test 绑定名）+ F2/M1/F4/F6/L2 → 全闭环；F3/L1 DEFER（文档归档）
  - 主节点修复后 re-review（F098/F099 先例）抓出 AC-B6 测试空洞 → 重写忠实化
  - 主节点 ephemeral venv 真 piper 冒烟 PASS（synthesize_wav + libopus 端到端，闭合 #1 盲区）
  - **0 HIGH 残留**；blast-radius gateway+core 2647/1-F106 + e2e_smoke 8/8
  - 注：本会话另一 worktree 17→14 个 runaway F091 pytest 进程 CPU 争用，致全量重跑 flaky（已用 blast-radius + 基线对照权威判定）
- GATE_VERIFY：**PAUSE → 待用户拍板 push（不主动 push，CLAUDE.md 硬约束）**
- 收官：completion-report + handoff（v0.2）+ living-docs（milestones F110 ✅ + M6 收官）COMPLETED
