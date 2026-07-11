# F137 门禁止血 — Research（gate 现状诊断 + provider 分发链解剖 + 竞品采纳）

**Feature ID**: F137 / `gate-triage`
**Milestone**: M9（质量保证体系）— P0，波次 1（F137 止血 ∥ F138 keystone 并行）
**Base**: master 8fb1386e / 分支 `feature/137-gate-triage`（worktree `.claude/worktrees/F137-gate-triage/`）
**上游依据**: `CLAUDE.local.md` §M9 战略规划 + `docs/blueprint/milestones.md` M9 节（line 613-639）+ 审计原始材料 `scratchpad/qa_audit_survivors.md`
**方法**: 全部 file:line 均在 worktree（= master 8fb1386e）实读核实；行号会漂，实施时以 grep 为准。

> ⚠️ 所有诊断均带证据。区分「已核实事实」与「设计推荐」——后者进 spec §7 岔路回用户拍板。

---

## A. Gate 现状四诊断（用户视角：commit / PR 时到底跑了什么）

### A.1 CI：唯一 workflow 断链（确认 HIGH，四诊断中唯一「常红」）

- 仓库唯一 workflow：`.github/workflows/feature-007-integration.yml`，触发于 PR + push（master/dev/feat/**/codex/**），paths 过滤 `octoagent/**`。
- 唯一测试步骤（`:39-40`）：`uv run pytest tests/integration/test_f007_e2e_integration.py -q`。
- **该文件已在 master 删除**（`git ls-tree -r origin/master | grep test_f007` → 0 命中）。pytest 对不存在路径**退出码 4**（usage error）→ CI job 常红或被忽略。
- **后果**：全仓 4600+ 测试**零 CI 覆盖**，回归 gate 完全靠本地 pre-commit（A.2）+ 各 Feature 人工全量回归。
- 依赖安装步骤（`:37`）：`uv sync --dev`——CI 是**干净 checkout**（无 worktree symlink 问题），可放心 `uv sync`（与 worktree PYTHONPATH 锁约束互不冲突：那条约束只针对开发者共享 venv）。

### A.2 pre-commit hook：工作正常，但只覆盖后端 e2e_smoke，零前端

- 装载机制（三层，已核实）：`Makefile:18-34` `install-hooks`（worktree-aware：linked worktree 只写 `--worktree` 级配置，主 worktree 写 common `.git/config`）→ 设 `core.hooksPath=.githooks`（另有 `repo-scripts/install-git-hooks.sh:8` 等价直设）。当前 worktree 实测 `git config --get core.hooksPath` = `.githooks`（生效）。
- `.githooks/pre-commit` 实际两段：
  1. **agent-config 同步检查**（`:31-43`）：`sync-agent-config.sh --check`，bypass = `SKIP_SYNC_CHECK=1`。刻意放在 SKIP_E2E **之前**（同步漂移与 e2e 环境是独立关注点）。
  2. **e2e_smoke 套件**（`:66-126`）：`uv run python -m pytest -m e2e_smoke --maxfail=1 -q`，180s python watchdog（SIGTERM→SIGKILL），bypass = `SKIP_E2E=1`。
- **关键执行模型坑（已在 hook 注释 `:83-91` 沉淀）**：用 `uv run python -m pytest` 而非裸 `uv run pytest`——裸 console-script 会逃出 venv，退化到全局 pytest（Homebrew 3.14 + SWE-bench `__editable__` 残留污染 sys.path + 缺 aiosqlite），逼迫每次 `SKIP_E2E`。`PYTHONNOUSERSITE=1` 双保险。
- **无环境时静默 SKIP**（`:20` 注释 + `fixtures_real_credentials.py:37-41`）：宿主缺 `~/.octoagent/auth-profiles.json` → e2e 自然 SKIP，不阻断 commit。确定性护栏在无凭证机器（含 CI）退化为**本地单机护栏**。
- **零前端**：pre-commit 完全不跑 `vitest` / `check-frontend-complexity`。

### A.3 前端门禁：护栏存在但失守无人知（实跑确认 FAIL）

- 脚本 `repo-scripts/check-frontend-complexity.mjs`（独立 node，walk 前端目录数行 vs 上限）。规则（`:8-56`）：
  - `explicitLimits`：AgentCenter 4800 / ControlPlane 4100 / SettingsCenter 1900 / index.css 3300。
  - 默认上限：pages/domains/ui `.ts(x)` = 1200；hooks/platform = 500；styles `.css` = 700。
- `package.json` scripts 齐备：`"test": "vitest run"`、`"check:complexity": "node ../../repo-scripts/check-frontend-complexity.mjs"`。devDeps 有 `vitest`，**无 playwright**。
- **实跑 `node repo-scripts/check-frontend-complexity.mjs`（本次核实）→ FAIL，3 违规**：
  - `pages/ChatWorkbench.tsx`: **1204 > 1200**
  - `hooks/useChatStream.ts`: **660 > 500**
  - `index.css`: **4477 > 3300**（脚本 `process.exit(1)`；此前误读 exit=0 是 `head` 的退出码）
- **无任何自动调用方**：`vitest run` 与 `check:complexity` 均不进 pre-commit / CI。护栏 FAIL 了但没有闸会失败。

### A.4 pyproject marker：描述与实现矛盾（误导测试选择/文档引用）

- `octoagent/pyproject.toml:70`：`"e2e_smoke: F087 smoke 5 域真实 LLM e2e（pre-commit hook 自动跑，≤ 180s）"`——**写「真实 LLM」**。
- 实现相反：`e2e_live/conftest.py:67` 注释 = `"e2e_smoke 集成层（不真打 LLM）：30s"`；`docs/codebase-architecture/e2e-testing.md` 亦确认 smoke 不打 LLM。
- 真打 LLM 的 `test_e2e_smoke_real_llm.py:20-21` 反而标 **e2e_full**（docstring：「不进 e2e_smoke——真打 LLM 不参与 pre-commit」）。
- **纯字符串矛盾**，无运行时后果，但按 marker 描述做测试选择或文档引用会得出错误结论。修复 = 改 marker 描述文案（+ 顺手核对 e2e_full/e2e_live 描述），零代码风险。

---

## B. Provider 分发链全解剖（硬闸落点 + Echo swallow 站点）

### B.1 单一分发点 `_dispatch`（硬闸落点，比 pydantic-ai 7 处便宜）

`provider_client.py`（`packages/provider/src/octoagent/provider/`）三层调用链：
- `call()`（`:315`）：瞬态传输错误有界指数重试外壳 → 调 `_dispatch_with_auth_refresh`。
- `_dispatch_with_auth_refresh()`（`:389`）：auth resolve + 401/403 force-refresh 重试一次 → 调 `_dispatch`。
- **`_dispatch()`（`:452`）：唯一 fan-out 点**——按 `transport` 路由到 3 协议实现：
  - `_call_openai_responses`（`:496`）/ `_call_openai_chat`（`:746`）/ `_call_anthropic_messages`（`:994`），每个内部才真发 `self._http.post(...)`。
- **硬闸就植在 `_dispatch` 入口**（在 3 transport 分叉之前、任何网络 I/O 之前）。pydantic-ai 要植 `openai.py` 6-7 处 request 入口（`models/openai.py:831,868,1796`），我们**一处 = 全覆盖**。

### B.2 第二网络入口 `embed()`（不走 `_dispatch`，独立 leak 面）

- `embed()`（`provider_client.py:912`）内部 `self._http.post(...)`（`~:958`）——**绕开 `_dispatch`**。
- 漏网的 embedding 真调用同样烧钱 + 打网络。硬闸若只在 `_dispatch`，embedding leak 不被拦。→ **推荐同点加闸**（2 处 = 全部网络出口，仍远比 pydantic-ai 便宜）。spec §7 岔路②确认范围。

### B.3 两条路径到达 `_dispatch`

- **路径 1 — FallbackManager 文本路径**（主聊天非工具 / context compaction）：
  `LLMService.call`（`llm_service.py`）→ `FallbackManager.call_with_fallback`（`:327`）→ `primary.complete()`。生产 primary = `ProviderRouterMessageAdapter`（`router_message_adapter.py:69` → `resolved.client.call()`）→ `ProviderClient.call` → `_dispatch`。**此路径有 Echo swallow**（见 B.4）。
- **路径 2 — SkillRunner 工具/决策环路径**：
  `ProviderModelClient`（`provider_model_client.py:596` `await resolved.client.call(...)`）→ `ProviderClient.call` → `_dispatch`。**无 FallbackManager 包裹**，但上游 `LLMService._try_call_with_tools` 有 `except Exception: return None`（B.4）。

### B.4 ★★★ 两个 Echo swallow 站点（友军误伤的根源，硬闸设计的关键）

`FallbackManager.call_with_fallback`（`fallback.py:38`）：
- `:66-71` 试 `primary.complete()`；`:72` **`except Exception as e`** 捕获**一切** → 降级 `fallback.complete()`（Echo，`:99-116`）→ 返回 `is_fallback=True` 的假成功。
- **唯一例外已存在**（`:74-85`）：`isinstance(e, LLMCallError) and e.status_code in (401,403)` → **`raise`（跳过 fallback）**。注释原文：「Echo 假成功会把事故掩盖成正常回复……让凭证断链的 task 永远到不了 FAILED 终态」。

`LLMService._try_call_with_tools`（`llm_service.py:445-457`）：
- `:452` `except SkillAuthError: raise`（注释 `:453-454`：「落进宽捕获会变成 return None → FallbackManager(Echo) 假成功」）。
- `:456` **`except Exception: return None`** → 上游 `call()`（`:323-331`）见 `None` 落到 `FallbackManager.call_with_fallback` → Echo。

**结论**：硬闸异常若是裸 `RuntimeError`，会被这两站点**任一**吞进 Echo → 完全复现 bench TLS 事故形态（真调用被静默退 Echo 假绿）。→ 硬闸异常必须是**专用类型**，且这两站点（+ grep 出的任何同类）必须像 `SkillAuthError`/401-403 一样**先 re-raise**。

### B.5 三个 FallbackManager 构造分支（决定哪些测试会碰 `_dispatch`）

`octo_harness.py:747-779`：
- **override 分支**（`:749-751`）：`primary=_llm_adapter_override`, `fallback=Echo`——e2e 注入的 adapter。
- **echo 分支**（`:758-762`）：`primary=EchoMessageAdapter()` **直接**, `fallback=None`——**`_dispatch` 永不到达**（Echo primary 不碰 ProviderClient）。→ **L3 Echo-mode 测试构造性安全**，硬闸不影响。
- **provider_direct 分支**（`:769-773`）：`primary=ProviderRouterMessageAdapter(router)`, `fallback=Echo`——真路径，`_dispatch` 可达，**Echo swallow 活跃**。
- `OCTOAGENT_LLM_MODE=echo` 走 echo 分支（`:726,758`）。生产/e2e-real 走 provider_direct。

**友军误伤精确场景**：测试用 provider_direct harness（非 echo mode、无 llm_adapter override）**但无真凭证 / 硬闸置 deny** → `primary.complete()` → `_dispatch` → 硬闸 raise → FallbackManager `except Exception` → Echo 假成功。**这正是硬闸要炸的场景**（bench 事故形态），也正是必须让异常 re-raise 的原因。

---

## C. 「合法降级」vs「漏网真调用」的信号区分（spec §7 岔路②内核）

| 维度 | 合法降级（Constitution #6，**保留**）| 漏网真调用（**必炸**）|
|------|-----------------------------------------|------------------------|
| 触发 | 真请求**已发到线**，provider/网络真失败（500 / TLS ReadError / 连接层）| 硬闸在**发到线之前**拒绝——因测试环境声明「本套件不该打真 LLM」|
| 语义 | 运行时故障，Echo 兜底让系统不整体不可用 | 测试意图的 pre-flight 断言违反（配置错误，非运行时故障）|
| 异常 | 任意普通 `Exception`（transport error / `LLMCallError` 非 401/403）| 专用 `ModelRequestsNotAllowedError`（新类型）|
| FallbackManager 处置 | `except Exception` → Echo（**不变**）| `isinstance == ModelRequestsNotAllowedError` → `raise`（**新增守卫**，同 401/403 先例）|

**信号 = 异常类型**。合法降级永远是「请求发出后失败」；漏网是「gate 声明不许发却发了」。这与已存在的 401/403 skip-fallback 属**同一家族**（都因「Echo 假成功掩盖事故」而 propagate 不 mask）——硬闸异常是它天然的兄弟。生产默认 allow（从不设 deny env），合法降级路径**零改动**。

---

## D. e2e_live 真 LLM 开闸机制现状（L2 如何显式打真 LLM）

- `octo_harness_e2e` fixture（`helpers/factories.py:30-66`）：注入 `credential_store=real_codex_credential_store`（宿主 OAuth tmp 副本）+ `llm_adapter=None` → 落 provider_direct 分支（primary=ProviderRouterMessageAdapter）→ **真 `_dispatch`**。
- `real_codex_credential_store`（`fixtures_real_credentials.py:27-53`）：宿主缺 `~/.octoagent/auth-profiles.json` → `pytest.skip`（不 FAIL）。
- marker 分层（`e2e_live/conftest.py:66-71`）：smoke 30s（不打 LLM）/ full 240s（真打 GPT-5.5 think-low）。
- hermetic autouse（`:74-`）：清凭证 env + 重定向 `OCTOAGENT_*` 到 tmp。
- **开闸落点**：默认 DENY 放**顶层 `octoagent/conftest.py`**（session autouse，覆盖全 testpaths）；ALLOW opt-in 放 **`e2e_live/conftest.py`**（真 LLM 测试 fixture 置 True / 按 e2e_full marker 翻转）。`pyproject.toml` 无 `addopts`/`env`（仅 testpaths `:67`）→ 机制走 conftest 模块级 global，对齐 pydantic-ai `conftest.py:72`。

---

## E. 竞品采纳明细（qa_audit_survivors.md，带 file:line）

- **pydantic-ai `ALLOW_MODEL_REQUESTS`**（`models/__init__.py:901-938` check + `openai.py:831,868,1796` 6 入口 + `tests/conftest.py:72` 顶层置 False + `314-317` override context manager）：模块级布尔 + `check_allow_model_requests()` 植每个真 provider request 入口；漏网 → `RuntimeError`；opt-in 走 context manager / fixture；TestModel/FunctionModel 显式不受限。→ **F137 硬闸直接范本**，但我们单点 `_dispatch`（+`embed`）取代其 6-7 点。
- **cc-haha 三模式 lane**（`scripts/quality-gate/modes.ts:21-198` + `runner.ts`）：pr=hermetic / baseline+release=含 live / release 下 skip live → FAIL。→ **F141 主责**，F137 不 front-run；但 F137 CI 范围决策要为 F141 留接口（deterministic-only lane）。
- **cc-haha change-policy + coverage 三重门**（`change-policy.ts` + `coverage-thresholds.json`）：ratchet + changed-lines 90%。→ F141/F142，非 F137。F137 只做「护栏进闸 + 现 FAIL 阈值处置」。
- **DeepResearch 外部共识**：真 agent E2E 不能做二元 CI 门（tau-bench SOTA <50%，arxiv 2406.12045）；unit(fake)/integration(real) 边界=是否打真 LLM；CI 永不打真 API，真 LLM 收敛 weekly canary（SDK `gateway-model-health.yml`）。→ **F137 CI 范围 = deterministic 层（L4+L3），真 LLM 不进 per-PR CI**（岔路①推荐依据）。

---

## F. 「F137 是否部分已有 / 别重造」核实

| 件 | 现状 | F137 动作 | 重造? |
|----|------|-----------|-------|
| 硬闸 `ALLOW_MODEL_REQUESTS` 等价 | grep `ALLOW_MODEL_REQUESTS`/`model_request_gate`/`ModelRequestsNotAllowed` → **0 命中** | **新建**（唯一真新增） | 否，全新 |
| CI workflow | 存在但断链（单文件已删） | **修 + 扩**（换成 deterministic 层）| 否，修复非重造 |
| pre-commit hook | 工作正常（e2e_smoke + sync check）| **补挂**前端 complexity | 否，复用 |
| `check-frontend-complexity.mjs` | 存在、可跑、当前 FAIL、无调用方 | **接线** + 处置 3 FAIL 阈值 | 否，勿改脚本主体 |
| `vitest run` script | `package.json` 已有 | **接线**进 CI | 否，勿重造 |
| marker 描述 | 矛盾 | **改文案** | 否，纯文档 |

**结论**：F137 = 1 件真新建（硬闸）+ 3 件接线/修复（CI/前端/marker）。绝大部分是「把已有但未接的护栏接进闸」，勿重造脚本/CI 骨架。

---

## G. 宪法对齐自查（#6 / #9 / #10，spec §6 展开）

- **#6 优雅降级**：硬闸**不碰**合法降级路径——生产默认 allow，FallbackManager→Echo 语义在真故障时**逐字节不变**（C 表）。硬闸只在测试声明 deny 时对「漏网真调用」炸。CI/前端门禁失败**不影响运行时降级**（都是 build/commit-time gate）。
- **#9 禁硬编码替代 LLM 决策**：硬闸是**测试基础设施开关**（是否允许真 LLM 网络调用），**不参与任何 Agent 决策路径**——不改 model 选择、不改 tool 决策、不注入关键词规则。生产从不置 deny，Agent 行为零感知。
- **#10 Policy-Driven / 认证单入口**：硬闸不碰认证（front_door / auth_resolver 均不动），是正交的「网络调用许可」开关；不新增认证旁路。

---

## H. 关键实施风险（plan §风险展开）

1. **翻 deny 默认可能抖出存量假绿**：若有测试正靠 provider_direct + 无凭证 → Echo 假成功「通过」，翻 deny 后会暴露为 `ModelRequestsNotAllowedError`。→ plan 必含「顶层 conftest 置 deny 后跑全量，triage 每个新失败：是真假绿还是漏配 allow opt-in」。这是 0-regression 的真风险点。
2. **grep 漏 swallow 站点**：B.4 已知 2 处，但可能有第三处 broad-catch→Echo/None。→ plan 含 grep sweep（`except Exception` 邻近 Echo/`return None`/fallback）。
3. **CI 全量 4600 测试时长**：GH hosted runner 跑全量可能超时/慢。→ 岔路①里定 CI 跑哪些子集（deterministic 层，非全量真 LLM）。
4. **前端阈值放宽 vs 修代码撞 F143**：F143 明确要下沉 ChatWorkbench/useChatStream——F137 若改这俩代码会抢 F143 范围（违「严格执行要求范围」）。→ 岔路③推荐放宽阈值 + 留 F143 ratchet 注释。

---

## I. 收窄期实测补遗（2026-07-11，rebase a1e4ca15 后勘察）

### I.1 ★ auth-refresh 时机实测（Fable 复审收窄注 a 的证据，决定闸点从 `_dispatch` 改 `call()`）

- `_dispatch_with_auth_refresh`（`provider_client.py:404-406`）：`return await self._dispatch(auth=await self._runtime.auth_resolver.resolve(), ...)`——auth resolve 在 `_dispatch` **之前**求值。
- `AuthResolver` 协议 docstring（`auth_resolver.py:44-51`）：「``resolve()``：**preemptive 检查 + 必要时刷新**（OAuth）」——**主动式**，非仅反应式 401 后刷新。
- `OAuthResolver` 类 docstring（`:115`）：「复用 Feature 078 的 ``is_expired()`` **5 分钟 preemptive buffer**」；`_resolve(force=False)` → `TokenRefreshCoordinator.refresh_if_needed` → `PkceOAuthAdapter.resolve(force_refresh=False)`——token 进 5 分钟过期窗即打真 OAuth token 端点（网络副作用）。
- 另有**反应式**路径：`_dispatch_with_auth_refresh:414-450` 收到 401/403 后 `force_refresh()` 再 `_dispatch` 一次。
- **结论**：闸在 `_dispatch` 太晚（preemptive refresh 已发生）；闸必须在 `call()`（`:315`）入口第一行。`grep -rn "_dispatch_with_auth_refresh\|\._dispatch("` 证实两私有方法仅被 `call()` 链路调用（`provider_client.py:352/405/442` 三处），production 无旁路。`embed()`（`:912`）不走 `call()` 且 `:942` 同有 `auth_resolver.resolve()` → 入口第一行同点加闸。
- **测试可从 `_dispatch` 直入**（如有直调私有方法的单测）——但那类测试用 fake http、无 auth 副作用，且属「直测 dispatch 机器」类（A.6 triage 规则②），不构成生产旁路。

### I.2 llm_service.py 额外 broad-catch 站点（A.4 sweep 已知候选）

`grep "except Exception"` 命中 `:456`（B.4 已知）外另有 `:619-621`（邻 `except SkillAuthError: raise`，同款结构候选）、`:690`、`:903`——Phase A.4 逐处人工核。

### I.3 benchmarks 调用形态（FR-9b 依据）

- OctoBench 真调用入口 = `benchmarks/runner/cli.py:297` argparse CLI（`octo-bench`），**非 pytest 进程** → pytest11 deny 插件构造性不触及；env 缺省 allow 保留。
- `benchmarks/tests/`（pytest 单测）自带独立 conftest（`benchmarks/tests/conftest.py` sys.path 注入 + `benchmarks/conftest.py` hermetic autouse），不在 octoagent testpaths 内；rootdir 独立 → 根 conftest 布线不触及，但 entry-point 插件（安装后）会触及 → 纳入 A.6 triage。
- bench runner 的 hermetic 协议（`benchmarks/conftest.py`）清凭证 env 但**不清 `OCTOAGENT_ALLOW_MODEL_REQUESTS`**（该 env 非凭证、非 `_API_KEY`/`_TOKEN` 后缀）→ CLI 真跑不受影响。

### I.4 baseline（AC-9 锚点）

- 选择器：`octoagent/apps octoagent/packages octoagent/tests --ignore=octoagent/apps/gateway/tests/e2e_live`，PYTHONPATH 锁 worktree + `--no-sync` + `-p no:cacheprovider`。
- 实测：**4846 passed / 11 skipped / 1 xfailed / 1 xpassed，168s**（e2e marker 实测只存在于 e2e_live 目录内——目录 ignore 即可保证回归跑零真 LLM，宿主有真凭证）。

### I.5 pre-commit hook 执行模型对本 Feature 的两个约束（防御依据）

- hook（`.githooks/pre-commit:95`）在 worktree 内跑 `uv run python -m pytest -m e2e_smoke`（cwd=worktree octoagent/，**无 `--no-sync`**）：收集 worktree conftest / 测试文件，但 venv editable 指 master src → 新 conftest 布线 import 新 gate 模块必须防御式（pre-merge 窗口 ImportError → no-op）。
- 同因（无 `--no-sync`）：worktree 内 pyproject 变更（新增 entry-points）可能触发 hook 的 uv 对共享 venv 重同步——与 F109/F110 改 optional deps 同型，非新风险；`uv lock --check` 核对 lock 是否需一并 commit（6543de0b 教训）。

### I.6 provider/__init__.py 反向依赖 gateway（插件模块选址依据）

`provider/__init__.py` 顶层 `from octoagent.gateway.services.config.dotenv_loader import load_project_dotenv`——provider 包 `__init__` import 会拉起 gateway。任何 in-package 插件模块 import 都无法绕过包 `__init__`（importlib 直载文件会产生第二份模块实例、`_allowed` 全局分裂→闸失效，**禁止**）。**实测**：`import octoagent.provider` 冷启动 1.10s（gateway 随之 0s 增量）——插件在 pytest 启动期加载的一次性成本可接受（全量跑本来就要 import；小范围包级跑 +1.1s）。**设计**：插件模块顶层零 import（stdlib 除外），gate import 放 `pytest_configure` 内（strict，不 try/except——插件与 gate 同包，插件可见 ⟹ gate 存在；缺失时响亮失败是正确行为）。

**跨 worktree 窗口 hazard（归档 + 逃生门）**：F137 合入 master 且主 venv `uv sync` 注册 entry point 后，基于 pre-F137 master 的旧 worktree（如并行中的 F138）PYTHONPATH 锁跑 pytest 时，插件模块经 PYTHONPATH 解析到旧 worktree 的 provider 包（无 `testing` 子模块）→ pytest 启动期 ImportError 响亮失败。**自愈路径 = rebase master**（旧 worktree 收尾前本来必做）；**临时逃生门** = `-p no:octoagent_model_request_gate`。窗口窄（合并串行 + rebase 常规化）、错误响亮非静默，接受并进 completion-report 提示 F138。
