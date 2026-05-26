# F103b Plan — Phase 详细实施步骤

> 上游：spec.md
> Baseline: `def6638` (F103 完成 push origin/master)
> Branch: `feature/103b-blueprint-limitations`
> 与 F103c 并行硬约束：不动任何 .py / .ts / .tsx 文件

---

## 0. Phase 0 实测侦察（spec/plan 阶段已完成）

### 0.1 3 文件章节锚点确认

- **core-design.md（913 行）**：
  - §8.5 Tooling 末尾在 line ~498（§8.5.6 后）→ 新增 §8.5.7 锚点
  - §8.6 Policy Engine 末尾在 line ~607（§8.6.5 后）→ 新增 §8.6.6 锚点
  - §8.7 Memory 末尾在 line ~720（§8.7.5 后）→ 新增 §8.7.6 锚点
  - §8.9 Provider Plane 起 line 768 → 整段重写（保留标题但内容替换为 ProviderRouter）
  - 文件末尾（line ~913）→ 新增 §8.10 NotificationService + DailyRoutineService

- **deployment-and-ops.md（564 行）**：
  - §12.1 部署拓扑末尾在 line ~92（§12.1.3 后）→ 新增 §12.1.4 锚点
  - §12.9.1 `octo config` 段在 line 465-482 → 末尾增补 ProviderRouter 段

- **testing-strategy.md（162 行）**：
  - §13.1 测试基础设施末尾 line ~32 → 新增 §13.1.1 锚点
  - 文件末尾（line ~162，§13.10 后）→ 新增 §13.11 + §13.12

### 0.2 数据源 SoT 实测可访问性

```bash
ls docs/codebase-architecture/{harness-and-context,provider-direct-routing,e2e-testing,testing-concurrency}.md
ls .specify/features/{081-litellm-full-retirement,084-context-and-harness,101-notification-attention,102-proactive-followup}/
```

所有源文件 baseline def6638 上**预期存在**（Phase A 实施前再 grep 确认）。

### 0.3 F089 在 baseline def6638 状态实测（决定 AC-C3 详略）

```bash
# Phase C 实施前必跑
ls .specify/features/089-* 2>/dev/null
grep -r "mcp_supervisor\|MCPSupervisor" packages/ --include="*.py" -l 2>/dev/null | head -5
grep -rn "delete_config" packages/core/src/octoagent/mcp/ 2>/dev/null | head -5
```

按实测结果选 AC-C3 详略路径。

---

## 1. Phase A：core-design.md 同步（F084/F101/F102/F081）

### 1.1 步骤

1. **实测先行**：
   ```bash
   ls docs/codebase-architecture/harness-and-context.md
   ls docs/codebase-architecture/provider-direct-routing.md
   cat CLAUDE.md | grep -A 30 "Feature 084 实施记录\|Feature 087 实施记录"
   ls packages/core/src/octoagent/harness/ 2>/dev/null
   ls packages/core/src/octoagent/services/ | grep -i "notification\|routine"
   ```
2. **读 F101 / F102 实施记录**：
   ```bash
   awk '/F101 实施记录/,/F102 实施记录/' .claude/worktrees/funny-cray-549fb6/.specify/features/../CLAUDE.local.md
   awk '/F102 实施记录/,/F103 实施记录/' .claude/worktrees/funny-cray-549fb6/.specify/features/../CLAUDE.local.md
   ```
   （实际从 CLAUDE.local.md 直接读取，按 §"M5（13 Feature，4 阶段）" 表内 F101/F102 段）
3. **修订 core-design.md** 按 AC-A1~A4 + AC-B3 顺序：
   - §8.5.7 Harness Layer（FR-A1 ≥ 80 行）
   - §8.6.6 ApprovalGate WAITING_APPROVAL（FR-A2 ≥ 40 行）
   - §8.7.6 Context Layer USER.md SoT（FR-A3 ≥ 60 行）
   - §8.9 Provider Plane 整段重写（FR-A5 ≥ 50 行）
   - §8.10 Notification + Routine（FR-A4 ≥ 100 行）
4. **commit**：
   ```bash
   git add docs/blueprint/core-design.md
   git commit -m "docs(F103b-Phase-A): core-design.md 同步 F084 Harness + F101 NotificationService + F102 DailyRoutineService + F081 ProviderRouter"
   ```
5. **回归**：
   ```bash
   uv run pytest -m e2e_smoke -x 2>&1 | tail -20
   ```
6. **Codex review per-Phase**（按 CLAUDE.local.md §Codex Adversarial Review）：
   - 命令：`/codex:adversarial-review`（foreground）
   - 范围：Phase A diff
   - 处理 high/medium，闭环后再进 Phase B
   - **F103 fallback 可用**：若网络中断，主 session 按 spec §8 review 重点接管

### 1.2 数据源对照表（FR → 数据源）

| FR | 修订点 | 数据源 |
|----|--------|--------|
| FR-A1 §8.5.7 Harness Layer | ToolRegistry / ToolsetResolver / ThreatScanner / SnapshotStore / ApprovalGate / DelegationManager | `docs/codebase-architecture/harness-and-context.md` + CLAUDE.md "Feature 084 实施记录" + `packages/core/src/octoagent/harness/` 实际代码 |
| FR-A2 §8.6.6 ApprovalGate | WAITING_APPROVAL 状态机 + SSE + startup recovery | CLAUDE.local.md F101 实施记录 + `.specify/features/101-notification-attention/spec.md` |
| FR-A3 §8.7.6 Context Layer | USER.md SoT + user_profile 三工具 + Memory Candidates API + WriteResult | `docs/codebase-architecture/harness-and-context.md` + CLAUDE.md "Feature 084 实施记录" |
| FR-A4 §8.10.1 NotificationService | 4 级优先级 + quiet hours + dismiss + NOTIFICATION_DISPATCHED | CLAUDE.local.md F101 实施记录 + `.specify/features/101-notification-attention/` |
| FR-A4 §8.10.2 DailyRoutineService | cron + 9 步 + LLM/fallback + token budget + USER.md 3 字段 + ROUTINE_* × 4 | CLAUDE.local.md F102 实施记录 + `.specify/features/102-proactive-followup/` |
| FR-A5 §8.9 Provider Plane 重写 | ProviderRouter 直连 + 3 transport + alias 解析 + 凭证 | `docs/codebase-architecture/provider-direct-routing.md` + `.specify/features/081-litellm-full-retirement/` |

### 1.3 风险与缓解

- **风险 1：core-design.md 单次 commit 改动量大（≥ 300 行新增）**，review 负担重 → 缓解：5 个新增章节 + §8.9 重写各自语义独立，commit message 列清楚每段；Codex review 聚焦内容准确性
- **风险 2：§8.9 重写可能误删保留段落** → 缓解：先读 §8.9 完整原文 → 决定保留 §8.9.1 语义 alias / 重写 §8.9.2-8.9.4 / 末尾追加 Multi-Transport 段
- **风险 3：FR-A5 与 FR-B1 重复表述** → 缓解：core-design.md §8.9 写"概念架构"；deployment-and-ops.md §12.1.4 写"部署运维细节"，明确分工

---

## 2. Phase B：deployment-and-ops.md 同步（F081）

### 2.1 步骤

1. **实测先行**：
   ```bash
   grep -n "litellm\|LiteLLM\|4000" docs/blueprint/deployment-and-ops.md | head -20
   sed -n '15,95p' docs/blueprint/deployment-and-ops.md
   ```
2. **修订 deployment-and-ops.md** 按 AC-B1~B2 顺序：
   - §12.1.4 ProviderRouter 直连（FR-B1 ≥ 40 行）
   - §12.9.1 末尾追加 ProviderRouter（FR-B2 短段）
3. **commit**：
   ```bash
   git add docs/blueprint/deployment-and-ops.md
   git commit -m "docs(F103b-Phase-B): deployment-and-ops.md 同步 F081 ProviderRouter 直连"
   ```
4. **回归 + Codex review**：同 Phase A

### 2.2 数据源对照表

| FR | 修订点 | 数据源 |
|----|--------|--------|
| FR-B1 §12.1.4 ProviderRouter 直连 | 退役 LiteLLM Proxy + 3 transport + docker-compose 简化 + auth-profiles.json | `docs/codebase-architecture/provider-direct-routing.md` + `.specify/features/081-litellm-full-retirement/spec.md` |
| FR-B2 §12.9.1 octo config 补充 | ProviderRouter alias / auth-profiles.json 注入 / 历史 LiteLLM 迁移说明 | F081 migrate-080 命令文档 |

### 2.3 风险与缓解

- **风险 1：docker-compose 真实状态可能未删 litellm 服务** → 缓解：实施前 grep `docker-compose.yml` 确认；若未删，§12.1.4 仅写"建议同步删除 litellm 服务（在 F104 部署阶段执行）"
- **风险 2：§12.1.4 与 §8.9 内容重复** → 缓解：§12.1.4 聚焦"部署运维"（service 启动顺序、端口、容器）；§8.9 聚焦"软件架构"

---

## 3. Phase C：testing-strategy.md 同步（F083/F087/F089）

### 3.1 步骤

1. **实测先行**：
   ```bash
   ls .specify/features/089-* 2>/dev/null && echo "F089 spec exists" || echo "F089 spec not found"
   ls .specify/features/087-* 2>/dev/null
   ls .specify/features/083-* 2>/dev/null
   grep -l "MCPSupervisor\|mcp_supervisor" packages/ apps/ -r --include="*.py" 2>/dev/null | head -5
   ls apps/cli/src/octoagent_cli/commands/e2e.py 2>/dev/null
   ls docs/codebase-architecture/{e2e-testing,testing-concurrency}.md
   ```
2. **按实测结果决定 AC-C3 详略**：
   - F089 完整存在 → FR-C3 ≥ 50 行
   - F089 部分/未启动 → FR-C3 ≤ 30 行 + 标注引用
3. **修订 testing-strategy.md** 按 AC-C1~C3 顺序：
   - §13.1.1 测试并发优化（FR-C1 ≥ 30 行）
   - §13.11 E2E Live Test Suite（FR-C2 ≥ 80 行）
   - §13.12 MCP E2E Testing（FR-C3 视实测）
4. **commit**：
   ```bash
   git add docs/blueprint/testing-strategy.md
   git commit -m "docs(F103b-Phase-C): testing-strategy.md 同步 F083 测试并发 + F087 e2e_live + F089 MCP E2E"
   ```
5. **回归 + Codex review**：同 Phase A

### 3.2 数据源对照表

| FR | 修订点 | 数据源 |
|----|--------|--------|
| FR-C1 §13.1.1 测试并发优化 | thread shutdown hang 修 / asyncio executor / xdist opt-in | `docs/codebase-architecture/testing-concurrency.md` + CLAUDE.md "Feature 083 实施记录" |
| FR-C2 §13.11 E2E Live | OctoHarness 4 DI + 13 能力域 + hermetic 隔离 + octo e2e CLI + pre-commit hook | `docs/codebase-architecture/e2e-testing.md` + CLAUDE.md "Feature 087 实施记录" |
| FR-C3 §13.12 MCP E2E | supervisor / delete_config / leak detection / pyt psutil | `.specify/features/089-*/spec.md`（如存在）+ baseline 代码实际状态 |

### 3.3 风险与缓解

- **风险 1：F089 在 baseline def6638 可能未完整落地** → 缓解：Phase C step 1 必跑实测；AC-C3 详略动态决定
- **风险 2：§13.11 与 §13.4/13.5 内容重复**（既有 integration/orchestration test）→ 缓解：§13.11 强调"e2e_live live test 是端到端集成"，与既有 13.4/13.5 是同主题不同 scope（live = 真 API + 真依赖；既有 integration 是 mocked）

---

## 4. Final Phase：Codex review + 回归 + 归总

### 4.1 步骤

1. **rebase F103c 完成的 master**（如已 push）：
   ```bash
   git fetch origin master
   git rebase origin/master
   # 解冲突：F103b 不动 .py，F103c 不动 docs/blueprint/，理论无冲突
   ```
2. **Final cross-Phase Codex review**：
   - 命令：`/codex:adversarial-review`（foreground，cross-phase 全 diff）
   - 范围：3 Phase 合并 diff（A + B + C）
   - 重点：spec §8 列的 6 项
3. **若 Codex 网络中断**：主 session 接管 review（按 F103 fallback pattern）：
   - 主 session 自行按 spec §8 review 重点逐项检查
   - 对照 baseline def6638 实际代码验证内容准确性
   - 产出 `codex-review-final.md`（fallback 标记 + finding 列表 + 闭环措施）
4. **处理 finding**：
   - high：必修
   - medium：修或归档下游 Feature（带 commit message 显式说明）
   - low：可忽略，commit message 记录
5. **全量回归**：
   ```bash
   uv run pytest packages/ apps/ -m "not slow and not e2e_live" --tb=line 2>&1 | tail -30
   ```
   期望：≥ 3649 passed（vs F103 baseline）
6. **e2e_smoke 5x 循环**（CLI）：
   ```bash
   octo e2e smoke --loop=5
   ```
7. **写 completion-report.md + handoff.md**（见 §5）
8. **不主动 push**：归总报告等用户拍板

### 4.2 与 F103c 协调

- 若 F103c 已 push origin/master：F103b Final 阶段 rebase；理论无冲突（不交叉文件）
- 若 F103c 未完成：F103b 完成后通知用户 + 等 F103c 完成后再 push（用户拍板顺序）

---

## 5. 制品产出清单

| 制品 | 路径 | 阶段 |
|------|------|------|
| spec.md | `.specify/features/103b-blueprint-limitations/spec.md` | 已完成 |
| plan.md | `.specify/features/103b-blueprint-limitations/plan.md` | 本文档 |
| tasks.md | `.specify/features/103b-blueprint-limitations/tasks.md` | 待写 |
| core-design.md 修订 | `docs/blueprint/core-design.md` | Phase A |
| deployment-and-ops.md 修订 | `docs/blueprint/deployment-and-ops.md` | Phase B |
| testing-strategy.md 修订 | `docs/blueprint/testing-strategy.md` | Phase C |
| codex-review-final.md | `.specify/features/103b-blueprint-limitations/codex-review-final.md` | Final |
| completion-report.md | `.specify/features/103b-blueprint-limitations/completion-report.md` | Final |
| handoff.md | `.specify/features/103b-blueprint-limitations/handoff.md` | Final |

---

## 6. Git workflow

- **每 Phase 1 commit**（A / B / C 各 1 个；Final 阶段：Codex review 闭环改动 + completion/handoff/codex-review 文档 1-2 commit）
- **不 force push**
- **不主动 push origin/master**：完成后归总报告给用户，等用户拍板

### Commit message 模板

```text
docs(F103b-Phase-A): core-design.md 同步 F084 Harness + F101 NotificationService + F102 DailyRoutineService + F081 ProviderRouter

新增 §8.5.7 Harness Layer (F084) / §8.6.6 ApprovalGate WAITING_APPROVAL (F101) /
§8.7.6 Context Layer USER.md SoT (F084) / §8.10 Notification + Routine (F101+F102)；
§8.9 Provider Plane 整段重写为 ProviderRouter 直连 (F081)。
Block A 全部 AC（A1-A4 + B3）闭环。Codex review pre-Phase A: N high / M medium 处理。

Co-Authored-By: Claude
```

（Co-Authored-By 按 CLAUDE.local.md 要求不加）
