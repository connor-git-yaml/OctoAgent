# Implementation Plan: F137 门禁止血（Gate Triage）

**Feature ID**: F137 / `gate-triage`
**Spec**: 本目录 `spec.md`（v0.1 草案）
**Research**: 本目录 `research.md`（gate 现状 + 分发链解剖，带 file:line）
**Status**: **设计先行——待用户拍板 spec §7 三设计岔路后再进入实施**。本 plan 是拍板后的执行蓝图。
**规模**: S-M（偏 M，见 spec §8）

> ⚠️ 命中「重大架构变更」（触碰 CI + provider 分发 `_dispatch` + gateway 测试基础设施）→ 强制 **Codex（`codex review --base`，scoped 小 diff）+ Opus 双评审 panel**；每 Phase 后 0 regression vs master 8fb1386e；e2e_smoke 必过；worktree PYTHONPATH 锁禁 uv sync；不主动 push 等用户拍板。

---

## 0. 前置：worktree 验证环境（沿用 M7/M8 教训）

- worktree `.venv` 是 symlink 指向主仓 → **裸 `pytest` 跑的是 master src**。验证本 worktree 代码必须 **PYTHONPATH 锁 worktree**（memory `project_worktree_venv_symlink`），禁 `uv sync`。
- 跑测试用 `uv run --project octoagent --no-sync python -m pytest`（memory `project_pytest_invocation_env_pollution` + `project_precommit_hook_execution_model`：裸 `uv run pytest` 逃逸 venv，须 `python -m pytest`）。
- **pre-commit hook 跑 master 版本**（非 worktree 编辑）——commit 时 e2e_smoke 用主仓 src；worktree 代码验证靠 PYTHONPATH 锁。
- **设计先行阶段基本不需跑测试**；进入实施后 baseline：进 Phase A 前先记 master 8fb1386e 的 `pytest` passed 数（回归护栏）。
- **本 Feature 特有**：硬闸 Phase 后要**特意跑全量（gate=deny 生效）**观察是否抖出存量假绿——这是本 Feature 最关键的回归验证动作（不是常规 smoke）。

---

## 1. 依赖与顺序（Phase 图）

```
Phase 0  研究闭环 + 用户拍板 §7 三岔路（已产 spec/research，待拍板）
        │
        ▼
Phase A  provider 硬闸（最独立、最微妙、最先——F138/F141 前置）
        │  model_request_gate.py + ModelRequestsNotAllowedError
        │  + _dispatch/embed 植闸 + 两 swallow 站点 re-raise + grep sweep
        │  + 顶层 conftest deny 默认 + e2e_live allow opt-in
        │  ★ 跑全量（gate=deny）triage 存量假绿 ★
        │
        ├───────────────┬───────────────┐
        ▼               ▼               ▼
Phase B  CI 修复+建     Phase C  前端门禁     Phase D  marker 文案
  (workflow deterministic  (complexity→        (pyproject:70
   层 + gate=deny)         pre-commit+CI /      + e2e_full/live
        │                  vitest→CI /          核对)
        │                  放宽 3 阈值)          │ XS，纯文档
        │               │                       │
        └───────┬───────┴───────────────────────┘
                ▼
Phase E  双评审 panel（Codex + Opus）+ 全量回归 0 regression + e2e_smoke 8/8
        │
        ▼
Phase F  文档 + living-docs 漂移闸 + completion-report
         （顺手修 testing-strategy.md VCR/TestModel/LiteLLM 漂移愿景
          + e2e-testing.md 宣称的 secret_store/transport_factory/clock 从未存在）
```

**顺序理由**（先难后易 + 硬闸最先，沿用 F091/F129 Phase 优化经验）：
- **A 最先且独立成 Phase**：硬闸是唯一「重大架构变更」子项（触碰 provider 分发 + 全仓测试默认），最微妙（不误伤 Echo）、需单独跑全量 triage 假绿、是 F138/F141 前置——先做完 A 拿到干净基线，后续 B/C/D 才有稳固地基。**A 内部也有序**：先建 gate 模块 + 异常（无副作用）→ 植 `_dispatch`/`embed` → 两站点 re-raise + grep sweep → conftest deny/allow → 全量 triage。
- **B/C/D 并行**：文件完全不冲突（B=`.github/workflows/`；C=`.githooks/pre-commit`+`package.json`+`check-frontend-complexity.mjs`；D=`pyproject.toml`）。B 依赖 A（CI 要 gate=deny 生效）；C/D 独立于 A。
- **E/F**：双评审 + 文档收尾。

**并行机会**：B、C、D 三者文件不冲突，A 完成后可并行（单会话内顺序做也可，规模小）。

---

## 2. 每 Phase 详细（含 AC↔test 绑定）

### Phase A — provider 硬闸（核心，FR-1~9 / AC-1~4,10）

**A.1 建 gate 模块（无副作用，先建立信心）**
- 新建 `packages/provider/src/octoagent/provider/model_request_gate.py`：
  - `class ModelRequestsNotAllowedError(RuntimeError)`（岔路②待确认基类；message 含 opt-in 指引，如「测试若需真 LLM，用 e2e_full marker / allow_model_requests() context」）。
  - 模块级 `_ALLOWED: bool`，初始 = env `OCTOAGENT_ALLOW_MODEL_REQUESTS`（缺省 True）。
  - `check_model_requests_allowed() -> None`（deny 时 raise）。
  - `allow_model_requests(value: bool=True)` 上下文管理器 + `set_allow_model_requests(bool)` setter（供 conftest/fixture 用）。
- 导出到 `provider/__init__.py`（随包公开，供 conftest/e2e 消费；岔路②若定 test-only 则不导出——**推荐随包**，与 pydantic-ai 一致且 harness 要用）。
- 单测 `packages/provider/tests/test_model_request_gate.py`：deny→raise / allow→pass / context manager 进出 / env 默认。

**A.2 植闸（FR-3/4）**
- `provider_client.py:452` `_dispatch` 入口第一行 `check_model_requests_allowed()`（网络 I/O 前）。
- `provider_client.py:912` `embed` 入口同（岔路②确认纳入）。

**A.3 两 swallow 站点 re-raise（FR-5/6，★不误伤 Echo★）**
- `fallback.py`：`call_with_fallback` 的 `except Exception as e`（`:72`）**之前**插 `except ModelRequestsNotAllowedError: raise`（照 401/403 `:74-85` 结构）。
- `llm_service.py`：`_try_call_with_tools` 的 `except Exception: return None`（`:456`）**之前**插 `except ModelRequestsNotAllowedError: raise`（照 `SkillAuthError` `:452`）。
- 绑定 AC-2/AC-3：`test_fallback.py` 补「gate deny → propagate 不 Echo」+「普通 Exception → 仍 Echo」两例。

**A.4 grep sweep（FR-7，防第三处漏网）**
- `grep -rn "except Exception" packages apps --include=*.py` 交叉 `Echo`/`return None`/`fallback`/`is_fallback`，人工核每处是否会把 provider 失败 mask 成假成功；命中则加 A.3 同款守卫。留 sweep 清单进 completion-report。

**A.5 conftest deny 默认 + e2e allow opt-in（FR-8/9，★0-regression 关键★）**
- 顶层 `octoagent/conftest.py` 加 session autouse fixture：`set_allow_model_requests(False)`（覆盖全 testpaths）。
- `e2e_live/conftest.py` 加 opt-in（岔路②定 marker vs fixture）：e2e_full 真 LLM 测试翻 allow（在 harness 起前生效）；确保 `octo_harness_e2e` 依赖链的真 LLM 测试不被误炸。
- 绑定 AC-4：宿主有 OAuth → e2e_full 真跑；无 → SKIP。

**A.6 ★全量 triage（本 Feature 命脉）★**
- `uv run --project octoagent --no-sync python -m pytest`（PYTHONPATH 锁 worktree）跑全量（gate=deny 生效）。
- **逐个 triage 每个新失败**：是真假绿（该测试本不该打真 LLM 却打了 → 是被硬闸抓的真 bug，保留炸）还是漏配 allow opt-in（真 LLM 测试没翻 gate → 补 opt-in）。产 triage 清单进 completion-report。
- 目标：0 净回归 vs master baseline（真假绿修正后）。

### Phase B — CI 修复+建（FR-10/11 / AC-5，依赖 A）

- 按岔路①（推荐 B-lite）改 `feature-007-integration.yml`：
  - 保留 clean checkout + `setup-python 3.12` + `setup-uv` + `uv sync --dev`（CI 是干净环境，`uv sync` 安全）。
  - 测试步骤换成确定性层（岔路①子问题定：`-m "not e2e_full and not e2e_live"` 全量 vs 更窄子集）；显式 gate=deny（依赖 A.5 conftest 或 CI env 设 `OCTOAGENT_ALLOW_MODEL_REQUESTS` 不设=默认，但测试 conftest 已 deny）。
  - 视时长决定单 job vs 分 job（岔路①子问题）。
  - 可选：rename workflow 为通用名（如 `ci.yml`）——但保守起见先修现名，避免触发历史引用。
- 绑定 AC-5：CI 在 clean checkout 真跑不退出码 4；漏网真调用 FAIL（构造性）。
- **注意**：CI 首跑可能因 A.6 triage 未尽/环境差异抖出问题——B 依赖 A 完成后做。

### Phase C — 前端门禁进闸（FR-12~14 / AC-6,7，独立于 A）

- **complexity 进 pre-commit + CI**（岔路③推荐）：
  - `.githooks/pre-commit`：在 e2e_smoke 之前/之后加一段 `node repo-scripts/check-frontend-complexity.mjs`（亚秒级）；bypass `SKIP_FRONTEND_CHECK=1`。注意 hook 在 repo 根跑，node 脚本路径 `repo-scripts/`（非 octoagent/）。
  - CI（Phase B workflow）加一步跑 `check:complexity`（或直接 node 脚本）。
- **vitest 进 CI-only**（岔路③推荐）：CI 加 `cd octoagent/frontend && npm ci && npm run test`（需 setup-node）；**不进 pre-commit**（避免拖慢）。
- **处置 3 FAIL**（岔路③推荐 B 放宽）：改 `check-frontend-complexity.mjs` 的 `explicitLimits`/`defaultLimit`：加 `ChatWorkbench.tsx=1250`、`useChatStream.ts=700` 进 explicitLimits，`index.css` 3300→4600；每处加注释「F137 放宽兜底，F143 UI 变薄后 ratchet 回收」。
- 绑定 AC-6（超阈值/vitest 挂即拦）、AC-7（现 3 FAIL 收敛 PASS）。
- **不改** ChatWorkbench/useChatStream 代码（F143 范围）。

### Phase D — marker 文案（FR-15 / AC-8，XS 纯文档，独立）

- `octoagent/pyproject.toml:70`：`e2e_smoke` 描述改「集成层，pre-commit 自动跑，**不真打 LLM**，≤180s」。
- 核对 `e2e_full`（`:71`）/`e2e_live`（`:72`）描述与实现一致（e2e_full = 真打 LLM）。
- 绑定 AC-8：grep + 人工核对 marker 描述 vs `e2e-testing.md`/conftest。

### Phase E — 双评审 + 回归（AC-9）

- Codex `codex review --base master`（scoped diff）+ Opus spec-对齐专项 review（多评审 panel，重大架构变更节点）。
- 分歧项显式列「必须人裁」。硬闸 re-raise 守卫 + conftest deny 是 review 重点（最易引隐性回归）。
- 全量回归 0 净 regression vs master 8fb1386e；e2e_smoke 8/8。

### Phase F — 文档 + 漂移闸

- completion-report.md（实际做 vs 计划 + A.4 grep sweep 清单 + A.6 triage 清单 + Codex/Opus finding 闭环表）。
- living-docs 漂移闸（顺手，M9 执行约束点名）：
  - `docs/blueprint/testing-strategy.md`：标注 VCR/TestModel/FunctionModel/ALLOW_MODEL_REQUESTS/LiteLLM 等未落地/已退役愿景（F137 落地了 ALLOW_MODEL_REQUESTS 硬闸 → 更新该节为「已实现」，其余标 planned/F138-F142）。
  - `docs/codebase-architecture/e2e-testing.md`：修正宣称的 `secret_store/transport_factory/clock` DI 钩子从未存在（实际 5 钩子见 research）。
  - 如触碰 gate/CI 行为，同步 blueprint 相关描述。

---

## 3. 风险 + 回归护栏

| 风险 | 概率 | 影响 | 缓解 |
|------|------|------|------|
| 翻 deny 默认抖出未知数量存量假绿 | 中 | 中（规模上探）| A.6 全量 triage 逐个分类（真 bug 保留炸 / 漏 opt-in 补）；进 completion-report |
| grep 漏第三处 broad-catch→Echo | 低 | 高（漏网仍被吞）| A.4 系统 grep sweep + 双评审重点核 |
| 硬闸异常被 provider `except ProviderError` 意外捕获 | 低 | 高 | 岔路②推荐基类 `RuntimeError`（不入 ProviderError 链）；A.1 单测覆盖「异常穿透 provider 异常处理」|
| CI 全量时长超 GH 限制 | 中 | 低 | 岔路①子问题定子集/分 job；先小子集验证时长 |
| 前端阈值放宽掩盖真复杂度增长 | 低 | 低 | 注释 F143 ratchet；阈值只放到 current+小余量（仍挡新增长）|
| e2e_full opt-in 漏配导致真 LLM 测试被误炸 | 中 | 低（SKIP/FAIL 明显）| A.5 按 marker 驱动（单一信号）；AC-4 机械验收 |
| pre-commit 加 node 检查在无 node 环境炸 | 低 | 低 | node 缺失时降级 SKIP（同 e2e OAuth SKIP 范式，Constitution #6）|

**回归护栏**：每 Phase 后 0 净 regression vs master 8fb1386e；A.6 是本 Feature 独有的「gate=deny 全量 triage」硬门；e2e_smoke 8/8。

---

## 4. 双评审触发说明

- **触发点**：spec/plan 大改后（本次设计先行产出）回主 session；Phase A（硬闸，改 provider 分发 + 全仓测试默认）+ Phase B（改 CI）完成后。
- **Codex 侧重**：硬闸 re-raise 守卫是否覆盖全部 swallow 站点；deny 默认是否引隐性回归；CI 是否真跑。
- **Opus 侧重**：spec intent 对齐（#6 不误伤降级 / #9 不碰决策 / #10 不碰认证）；岔路推荐是否越界下游 Feature。
- **不主动 push**：全部 commit 到 `feature/137-gate-triage` worktree 分支，等用户读归总报告拍板。
