# F095 Worker Behavior Workspace Parity — Trace

baseline: 284f74d (F093 完成后)
分支: feature/095-worker-behavior-workspace-parity
worktree: .claude/worktrees/F095-worker-behavior-workspace-parity
F094 并行: feature/094-worker-memory-parity（独立 worktree）

## 编排决策

- research_mode: skip（需求来源 = M5 prompt + CLAUDE.local.md SoT）
- gate_policy: balanced（F093/F094 沿用）
- preset: 无覆盖

## Phase 进度

| 时间 | Phase | 状态 | 备注 |
|------|-------|------|------|
| skipped | Phase 1a/1b/1c/1d | research | 跳过（research_mode=skip）|
| completed | Phase 2 | specify v0.1 | spec.md 起草（含块 A 实测 + §6.1-6.5 决策）|
| completed | Phase 3.5 | GATE_DESIGN v0.1 | 用户拍板：spec 通过 + IDENTITY bug 顺手修 |
| completed | Phase 4 | plan v0.1 | plan.md 起草（A→B→C→D 顺序）|
| completed | baseline | test 计数 | `test_behavior_workspace.py` = 53 passed |
| completed | Codex review #1 | spec + plan adversarial | 5 high + 7 medium + 3 low，全接受；2 high 触发 USER/BOOTSTRAP 决策翻转 |
| completed | Phase 3.5 | GATE_DESIGN v0.2（决策翻转）| 用户拍板：USER 改扩入 / BOOTSTRAP 改不扩入 |
| completed | Phase 2/4 | spec/plan v0.2 重写 | 闭环全部 15 条 finding；Phase 顺序改 A→B→C→D→E（5 个 Phase）|
| in_progress | Phase 5 | tasks | tasks.md 起草 |

## 块 A 实测核心结论

1. baseline Worker 实际进 LLM 的 behavior 文件 = 4 个（不是 5），IDENTITY 被 envelope 二次过滤剥离
2. share_with_workers 是真实过滤层，与白名单 `AND` 叠加（agent_decision.py:329）
3. IDENTITY.worker.md 模板存在 + 派发路径完整，但 envelope 把 IDENTITY 剥了——模板渲染了 Worker 永远看不到（隐性 bug）
4. SOUL.worker.md / HEARTBEAT.worker.md / BOOTSTRAP.worker.md 模板均不存在
5. BEHAVIOR_LOADED 事件不存在（grep 0 命中）
6. F094 改动域（packages/memory/、recall preferences、agent_id）与 F095 工作域文件级低冲突（Codex M9 推动从"完全静态隔离"调整为"低冲突 + AC-7b 集成验证"）
7. **BOOTSTRAP.md 实测内容 = 主 Agent 用户首次见面对话脚本**（"你好！我是 __AGENT_NAME__"... "你希望我怎么称呼你"... 用 `behavior.propose_file` 改 IDENTITY/SOUL）→ 完全不适合 Worker
8. **USER.md 实测内容 = 用户长期偏好**（语言中文 / 信息组织 / 回复风格 / 确认偏好），无 user-facing 对话指令 → 适合 Worker

## Codex review #1 闭环

15 条 finding 全部接受。详见 codex-review-spec-plan.md。

**关键变更（v0.2）**：

- §6.1 USER.md → **扩入** Worker 白名单（v0.1 翻转）
- §6.2 BOOTSTRAP.md → **不扩入** Worker 白名单（v0.1 翻转）
- §6.4 最终白名单 8 文件 = `{AGENTS, TOOLS, IDENTITY, PROJECT, KNOWLEDGE, USER, SOUL, HEARTBEAT}`（去 BOOTSTRAP 加 USER）
- §6.5 BEHAVIOR_LOADED → BEHAVIOR_PACK_LOADED + BehaviorPack.pack_id 字段（让 F096 USED 可引用）
- AC-2b 加 prompt 拼接顺序断言
- AC-4 覆盖所有 Worker 创建入口
- AC-5 加 pack_source / pack_id payload
- AC-6 扩展为所有 non-WORKER profile 行为零变更
- AC-7b 加 F094/F095 双 agent_id 集成验证
- Phase 顺序改 A→B→C→D→E（B 模板先于 C 白名单扩展，避免 Worker 中间态看通用 SOUL/HEARTBEAT）
- plan §0.6/0.7/0.8/0.9 增 4 项实测前置任务
- spec §10 拆分"已决策"与"待完成"

## 关键决策（spec §6 v0.2）

- §6.1 USER.md → **扩入** Worker 白名单（v0.2 翻转）
- §6.2 BOOTSTRAP.md → **不扩入** Worker 白名单（v0.2 翻转）
- §6.3 share_with_workers → **保留字段（UI 用），去掉 envelope 过滤**；shared_file_ids 字段名保留 + docstring 显式语义变更
- §6.4 最终白名单 = 8 文件（FULL 9 - BOOTSTRAP）
- §6.5 BEHAVIOR_PACK_LOADED + pack_id（F095 范围 minimal，USED 留 F096）

## 制品

- spec.md v0.2
- plan.md v0.2
- codex-review-spec-plan.md（review #1 闭环表）
- trace.md（本文件）
- tasks.md（in progress）
