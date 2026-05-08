# F093 接手说明（下一 session 启动入口）

> 本 session 完成「设计阶段」（spec → plan → tasks），未启动 Phase 6 Implement。
> 下一 session 接手时按本文档操作即可零返工。

## 1. 工作目录与分支

- **Worktree**: `/Users/connorlu/Desktop/.workspace2.nosync/OctoAgent/.claude/worktrees/F093-worker-full-session-parity`
- **Branch**: `feature/093-worker-full-session-parity`（base origin/master @ 7e52bc6 = F092 baseline）
- **Repo root in worktree**: `<Worktree>/octoagent/`（注意嵌套一层）

下一 session 启动命令：
```bash
cd /Users/connorlu/Desktop/.workspace2.nosync/OctoAgent/.claude/worktrees/F093-worker-full-session-parity
git status   # 应显示分支干净 + 1 commit ahead of origin/master（设计制品）
```

## 2. 已交付制品（设计阶段 SoT）

| 制品 | 路径 | 行数 | 用途 |
|------|------|------|------|
| spec.md | `.specify/features/093-worker-full-session-parity/spec.md` | 336 | WHAT/WHY/不变量/acceptance |
| quality-checklist.md | 同目录 | 105 | 21 PASS / 0 WARN / 0 FAIL（spec 质量 baseline） |
| plan.md | 同目录 | 266 | 技术决策 + Phase 切分 + 拆分候选 C |
| tasks.md | 同目录 | 461 | Phase C/A/B/D 颗粒化任务清单（C-0~D-5） |
| trace.md | 同目录 | 30+ | 编排时间轴 |
| handoff.md | 本文档 | — | 接手入口 |

## 3. 已固定的关键决策（Open Points 终结）

| Open Point | 决策 |
|------------|------|
| 拆分边界 | **候选 C**：仅拆出 `agent_context_turn_writer.py` 作为 mixin（最小破坏面） |
| 块 A propagate gap | TDD：先写 e2e 失败测试，再视失败模式定位修补 |
| 事件复用 vs 新增 | 优先复用 baseline 事件 schema；如基线无则新增 `AGENT_SESSION_TURN_PERSISTED` |
| 块 B 范围 | 仅 round-trip + 互不污染 + extractor 不跑 worker 断言（不动 cursor 推进） |
| 迁移影响 | 已 grep 确认私有方法仅被 hook 内部调用；Phase C 起始再确认一次 |
| pre-commit hook | 不动；每 Phase commit 前手工跑 e2e_smoke |

## 4. Phase 实施顺序与工时

```
Phase C  ~2.5h  — agent_context.py 拆分到 turn-writer mixin
Phase A  ~4-5h  — Worker turn 写入端到端 + 隔离断言（含可能的 propagate 修补）
Phase B  ~2h    — Worker session 字段 round-trip + extractor 跳过
Phase D  ~2.5h  — Final Codex review + completion-report + 等用户拍板 push
```

**严格串行**：C → A → B → D（C 是 A 的脚手架；A 修通的 turn 链是 B 的前提；D 是验收）。

**Phase 内部并行点**（同 session 内可加速）：
- A-1 / A-2 / A-3 三测试文件可并行写
- B-1 / B-2 可并行

## 5. 下一 session 启动建议

### 5.1 推荐路径：直接跑 Phase C

下一 session 启动后，直接按 tasks.md Phase C 任务清单执行。**不要**重跑 spec/plan/tasks——它们已是 SoT。

启动 prompt 模板（在新 session 里输入）：
```
继续 F093 Worker Full Session Parity 实施。
SoT 文档全在 /Users/connorlu/Desktop/.workspace2.nosync/OctoAgent/.claude/worktrees/F093-worker-full-session-parity/.specify/features/093-worker-full-session-parity/

当前进度：设计阶段已完成（spec/plan/tasks 已 commit），
下一步进入 Phase 6 Implement，从 tasks.md Phase C 起步。

请按 tasks.md Task C-0 起跑（C-0 → C-1 → C-2 → C-3 → C-4 → C-5 → C-6），
每步完成回报；C-5 Codex review 用 codex:codex-rescue 子代理触发。
Phase C commit 后 pause 等用户拍板是否继续 Phase A。

关键约束（不要忘）：
- 不主动 push origin/master
- 不 force push
- Codex review HIGH 必修
- commit message 含 Codex finding 闭环说明
- 行为零变更（块 C），全量回归 0 regression vs 7e52bc6
```

### 5.2 备选路径：手动跑 tasks

按 tasks.md 每个 Task 描述操作。每 Task 完成后跑验证命令再进下一 Task。

## 6. 关键侦察结论（下次启动可直接信任，不用重复）

实测 F092 baseline (7e52bc6) 已确认：

1. **AgentSession 模型**已含 `rolling_summary` + `memory_cursor_seq`（packages/core/agent_context.py:303/306）。块 B 不需要加字段，只需补单测。
2. **AgentSessionKind** 已含 WORKER_INTERNAL / DIRECT_WORKER / SUBAGENT_INTERNAL（line 99-103）。
3. **AgentSessionTurnHook**（83 行，apps/gateway/.../agent_session_turn_hook.py）完全 agent-agnostic，仅依赖 `context.agent_session_id`。
4. **`_ensure_agent_session`**（agent_context.py:2229）已根据 `(AgentRuntimeRole.WORKER, parent_agent_session_id, work_id)` 三元组判定 worker session kind，复用或新建。
5. **`compiled_context.effective_agent_session_id`**（agent_context.py:905, 934）赋值 worker session id（worker 路径已实测）。
6. **`_build_llm_dispatch_metadata`**（task_service.py:870）从 `compiled_context.effective_agent_session_id` 注入 `dispatch_metadata["agent_session_id"]`。
7. **SkillExecutionContext**（llm_service.py:427）从 metadata 读 `agent_session_id`。
8. **`_append_agent_session_turn`**（agent_context.py:1764）完全 agent-agnostic，无 worker/main 过滤。

**结论**：propagate 链在 baseline **理论上已通**。F093 真实工作可能 90% 是 **补测试 + 拆分 D6**；如有少量 patch（A-4 e2e 测试可能暴露 patch 点），优先按 plan §0.1 8 跳找到具体断点。

## 7. Codex Adversarial Review 触发

CLAUDE.local.md §"Codex Adversarial Review 强制规则" 要求：

| 时机 | 模式 | 触发命令 |
|------|------|----------|
| pre-Phase 4 (plan.md 大改后) | foreground | `/codex:adversarial-review` 或 `codex:codex-rescue` 子代理 |
| per-Phase C / A / B commit 前 | foreground | 同上 |
| Final cross-Phase（D-2） | background | 同上 |

每条 finding 必须 commit message 闭环（含 N high / M medium / K low 处理结果）。

## 8. e2e_smoke pre-commit hook

F087 已建好但**当前 worktree 未安装** hook（`ls .git/hooks/` 空）。

**Phase C 启动前必做**：
```bash
cd /Users/connorlu/Desktop/.workspace2.nosync/OctoAgent/.claude/worktrees/F093-worker-full-session-parity/octoagent
make install-hooks
```

之后每 commit 自动跑 e2e_smoke 180s portable watchdog。紧急 bypass：`SKIP_E2E=1 git commit ...`（不建议常用）。

## 9. F092 baseline 数据

- Commit hash: `7e52bc6`
- 全量回归基数：参考 `.specify/features/092-delegation-plane-unification/completion-report.md`（约 3100 passed）
- 进入 Phase C 前先跑一次基线确认：
  ```bash
  cd octoagent && uv run pytest 2>&1 | tail -5
  cd octoagent && uv run pytest -m e2e_smoke 2>&1 | tail -5
  ```

## 10. 完成判据（spec §5）

不重复列；详见 spec.md §5 Acceptance Criteria（A1-A5 / B1-B4 / C1-C3 / G1-G7）。完成时必产出 completion-report.md（路径见 tasks.md Task D-3）。

## 11. 不变量（再次重申）

- **不主动 push origin/master**：F093 完成后归总报告等用户拍板
- **不 force push**
- **行为零变更（块 C）**：全量回归 0 regression vs 7e52bc6
- **新行为可观测可审计（块 A/B）**：事件 emit + 单测覆盖 + 隔离断言
- **Codex review HIGH 必修**
- **Phase 跳过显式归档**：commit message + completion-report 均要写

---

**接手说明完毕。下一 session 直接读本文档 + spec.md + tasks.md 三件套即可启动。**
