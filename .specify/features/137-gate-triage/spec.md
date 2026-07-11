# Feature Specification: F137 门禁止血（Gate Triage）

**Feature ID**: F137
**Slug**: gate-triage
**Milestone**: M9（质量保证体系：四层测试金字塔 + 门禁改造）— **P0**，波次 1（F137 止血 ∥ F138 keystone 并行）
**规模**: **S-M**（复核见 §8）
**Status**: **收窄版 v1.0**（三岔路已拍板 + Fable 5 复审三收窄注锁入；§7 从「待拍板」转为「拍板决策记录」；实施中）
**Base**: master a1e4ca15（rebase 自 8fb1386e，增量纯 docs）/ 分支 `feature/137-gate-triage`（worktree `.claude/worktrees/F137-gate-triage/`）
**上游依据**: `CLAUDE.local.md` §M9「首波设计先行完成 + 六岔路拍板」+「Fable 5 复审调整」+ `docs/blueprint/milestones.md` M9 节（line 613-639）+ 本目录 `research.md`（含 file:line 证据）+ `scratchpad/qa_audit_survivors.md`

> ⚠️ 命中「重大架构变更」节点（触碰 CI / provider 调用入口 `call()`/`embed()` / gateway 测试基础设施）→ spec/plan 大改后走 **Codex + Opus 双评审 panel**。本 spec 设计阶段先自查对齐宪法 #6/#9/#10（§6）。

---

## 0. 设计基础说明（实测核实，均带证据，详见 research.md）

### 0.1 ★ 核心定位：F137 是「把已有但未接的护栏接进闸 + 修断链 + 补 1 个构造性硬闸」，不是造测试体系

- M9 全局判断②：CI 断链、前端护栏失守、marker 矛盾、provider 无硬闸——**四处 gate 层硬伤，都是「已有能力没接进闸」或「接错了」**（research.md §A/§F）。
- F137 = 1 件真新建（provider 硬闸，全仓 grep 0 命中）+ 3 件接线/修复（CI / 前端门禁 / marker）。**勿重造** `check-frontend-complexity.mjs`（已能跑）、`vitest run`（script 已有）、CI 骨架（改非造）。
- **不做金字塔本身**：脚本化 LLM harness（L3 决策环）= F138；VCR = F139；L1 Playwright = F140；三模式 lane = F141。F137 只止血 gate 层，为它们铺地基（硬闸是 F138/F141 的前置构造性保证）。

### 0.2 ★★ 最微妙技术内核：硬闸不能误伤 FallbackManager→Echo 合法降级（research.md §B.4/§C）

- provider 有**两个 Echo swallow 站点**：`FallbackManager.call_with_fallback`（`fallback.py:72` `except Exception`→Echo）+ `LLMService._try_call_with_tools`（`llm_service.py:456` `except Exception: return None`→上游 Echo）。
- 硬闸异常若是裸 `RuntimeError` → 被任一站点吞进 Echo → **完全复现 bench TLS 事故**（真调用被静默退 Echo 假绿）。
- **硬结论**（非偏好，是约束）：硬闸异常必须是**专用类型** `ModelRequestsNotAllowedError`，且两站点（+ grep 出的同类）必须**先 re-raise**——与已存在的 401/403（`fallback.py:74-85`）/ `SkillAuthError`（`llm_service.py:452`）skip-fallback **同一家族、同一理由**（「Echo 假成功掩盖事故」）。
- **信号 = 异常类型**：合法降级 = 请求已到线后真失败（任意普通 Exception，Echo 兜底不变）；漏网 = gate 声明不许发却发了（专用异常，必炸）。

### 0.3 ★ 硬闸落点 = `call()` 入口 + `embed()` 入口（Fable 复审收窄 + 实测修正原 `_dispatch` 方案）

- **实测修正（2026-07-11 收窄时勘察）**：`_dispatch_with_auth_refresh`（`provider_client.py:404-406`）在进 `_dispatch` **之前**先 `await self._runtime.auth_resolver.resolve()`；而 `OAuthResolver.resolve()` 是**主动 preemptive 刷新**（`auth_resolver.py:44-53` 协议 docstring「preemptive 检查 + 必要时刷新」+ `:115`「is_expired() 5 分钟 preemptive buffer」→ `TokenRefreshCoordinator.refresh_if_needed` → `PkceOAuthAdapter.resolve(force_refresh=False)`，token 进 5 分钟过期窗即**打真 OAuth token 端点**）。
- **结论**：闸若植 `_dispatch`，deny 模式带过期凭证仍会在闸前打真 auth 端点——Fable 复审的担忧实测成立。**硬闸植 `call()`（`:315`）入口第一行**（早于 retry 外壳 + 早于 auth resolve），一处覆盖全部 3 transport chat 路径（grep 证实 `_dispatch`/`_dispatch_with_auth_refresh` 仅被 `call()` 链路调用，无旁路）。
- `embed()`（`:912`）是第二网络入口（不走 `call()`，且 `:942` 同样先 `auth_resolver.resolve()`）→ **入口第一行同点加闸**（2 处 = 全部网络出口，仍远比 pydantic-ai 6-7 处便宜）。

### 0.4 实测核实的可复用资产（勿重造，research.md §A/§F）

| 资产 | 位置（证据）| F137 如何用 |
|------|------------|-------------|
| pre-commit hook（e2e_smoke + sync check + worktree-aware 装载）| `.githooks/pre-commit` + `Makefile:18-34` | **补挂**前端 complexity（不改 e2e_smoke 主体）|
| `check-frontend-complexity.mjs`（walk+数行+阈值，可跑）| `repo-scripts/check-frontend-complexity.mjs` | **接线**进 gate + 处置 3 FAIL 阈值（勿改脚本主体）|
| `vitest run` script | `octoagent/frontend/package.json` `scripts.test` | **接线**进 CI |
| 401/403 skip-fallback 先例 | `fallback.py:74-85` + `llm_service.py:452` | 硬闸 re-raise 守卫的**范式模板**（照抄结构）|
| e2e 真 LLM harness + hermetic autouse | `factories.py:30-66` + `e2e_live/conftest.py:74-` | ALLOW opt-in 落点（真 LLM 测试翻 gate=True）|
| 顶层 `octoagent/conftest.py`（session 级）| `conftest.py` | DENY 默认落点（session autouse）|

### 0.5 实测核实的真实缺口（F137 新建/接线，research.md §F）

1. **provider 无构造性硬闸**（grep `ALLOW_MODEL_REQUESTS` → 0）——漏网真调用被 Echo 静默吞（唯一真新建）。
2. **CI 断链**（`feature-007-integration.yml:40` 引用已删文件 → 退出码 4，4600+ 测试零 CI）。
3. **前端门禁零自动闸**（complexity 现 FAIL 无人知；vitest 不进任何闸）。
4. **marker 描述矛盾**（`pyproject.toml:70` 写「真实 LLM」，实现不打 LLM）。

### 0.6 哲学守界（Constitution，research.md §G，§6 展开）

- **#6 降级**：硬闸不碰合法降级——生产默认 allow，FallbackManager→Echo 真故障语义逐字节不变；gate 失败是 build/commit-time，不影响运行时降级。
- **#9 禁硬编码替代 LLM 决策**：硬闸是测试基础设施开关（是否允许真 LLM 网络调用），**不参与任何 Agent 决策**——不改 model/tool 选择、不注入关键词规则。生产零感知。
- **#10 认证单入口**：硬闸不碰认证（front_door / auth_resolver 不动），正交「网络调用许可」开关，无认证旁路。

---

## 1. 目标（Why）

把 M9 判断②暴露的四处 gate 硬伤止血，让「commit / PR 时到底跑了什么护栏」重新可信、可自动、可扩展：

- **CI 从常红回到真跑**：修断链 workflow，让确定性层（L4 单元 + L3 无 LLM）在每次 PR/push 真跑，4600+ 测试重获 CI 覆盖底盘。
- **前端护栏进闸**：complexity 检查 + vitest 接进自动闸——护栏 FAIL 时**闸会失败**，不再「失守无人知」。
- **marker 名实相符**：改矛盾描述，测试选择/文档引用不再被误导。
- **漏网真调用必炸**：provider `call()` + `embed()` 入口加构造性硬闸——测试声明「不打真 LLM」时漏网的真调用**炸**而非被 Echo 静默吞（堵 bench TLS 事故根源），**且不误伤合法降级**。

**用户可感知的改变**：
- 打开 GitHub PR → 看到 CI check 真跑确定性测试并给绿/红（不再是断链常红）。
- `git commit` 前端超复杂度文件 / vitest 挂 → 被拦（可 bypass，同 SKIP_E2E 范式）——取决于 §7 岔路③。
- 跑 L3/L4 套件时若某测试意外发真 LLM 调用 → 立即 `ModelRequestsNotAllowedError` 炸（带清晰 message：怎么 opt-in），而非假绿。

---

## 2. 范围声明（4 件止血）

### IN（本 Feature 做）

1. **修断链 CI**（`.github/workflows/feature-007-integration.yml`）：换掉引用已删文件的步骤，跑确定性层（§7 岔路①定范围）。
2. **修 marker 矛盾**（`octoagent/pyproject.toml:70`）：改 `e2e_smoke` 描述文案为「集成层，不真打 LLM」；顺手核对 `e2e_full`/`e2e_live` 描述与实现一致。
3. **前端门禁进闸**：`check:complexity` + `vitest run` 接进自动闸（pre-commit / CI，§7 岔路③定落点）；处置当前 3 个 complexity FAIL（§7 岔路③定「放宽 vs 修代码」）。
4. **provider 硬闸**：新增 `ModelRequestsNotAllowedError` + 模块级 gate（默认 allow / env `OCTOAGENT_ALLOW_MODEL_REQUESTS`）+ 植 `call()`+`embed()` 入口 + Echo swallow 站点加 re-raise 守卫 + pytest11 插件/根 conftest 置 deny 默认 + e2e_full marker ALLOW opt-in。

### OUT（明确不做，归下游）

- 脚本化 LLM harness / L3 决策环覆盖（**F138**）；VCR 录制回放（**F139**）；L1 Playwright（**F140**）；三模式 pr/baseline/release lane + release 强制 live + change-policy 路由（**F141**）；coverage 三重门 / 第三方库语义钉住（**F142**）；ChatWorkbench/useChatStream 代码下沉（**F143**）。
- **不改** front_door / 认证 / Agent 决策环 / provider 真实调用逻辑（硬闸只加「许可检查」不改调用本身）。
- **不做** 真 LLM 进 per-PR CI（DeepResearch 共识：真 agent E2E 不能做二元 CI 门；收敛 weekly canary 是 F141 事）。

---

## 3. 功能需求（FR）

### 硬闸（核心）

- **FR-1**：新增专用异常 `ModelRequestsNotAllowedError`（provider 包，**继承 `RuntimeError`**——已拍板，勿继承 `ProviderError` 防被现有 `except ProviderError` 链误吞；message 含「如何 opt-in」指引）。
- **FR-2**：新增模块级 gate `provider/model_request_gate.py`：`check_model_requests_allowed()`（deny 时 raise FR-1）+ `allow_model_requests()` 上下文管理器 + `set_allow_model_requests(bool)` setter + 初始默认从 env `OCTOAGENT_ALLOW_MODEL_REQUESTS` 读（缺省 = allow=True，**生产不受影响**）。gate 为进程内全局；子进程回落 env 默认（limitation 归档 §8）。
- **FR-3**：`ProviderClient.call`（`provider_client.py:315`）**入口第一行**调 `check_model_requests_allowed()`——早于瞬态 retry 外壳与 `auth_resolver.resolve()` 的 preemptive refresh 副作用（§0.3 实测：deny 带过期凭证不得打真 auth 端点）。
- **FR-4**：`ProviderClient.embed`（`:912`）入口第一行同样调 `check_model_requests_allowed()`（`:942` 同有 auth resolve，同理须在其前）。
- **FR-5**：`FallbackManager.call_with_fallback`（`fallback.py:72` `except Exception` 块内）加 `isinstance(e, ModelRequestsNotAllowedError) → raise` 守卫（**先于**降级到 Echo），照 401/403 先例（`:74-85`）。
- **FR-6**：`LLMService._try_call_with_tools`（`llm_service.py:456` 之前）加 `except ModelRequestsNotAllowedError: raise`，照 `SkillAuthError`（`:452`）先例。
- **FR-7**：grep sweep 全仓「broad `except Exception` 邻近 Echo / `return None` / fallback」站点（已知候选：`llm_service.py:619-621` / `:690` / `:903`），凡会把 provider 失败 mask 成 Echo/假成功的，均加 FR-5/6 同款守卫（防第三处漏网）；sweep 清单进 completion-report。
- **FR-8**（已拍板：**pytest11 entry-point 插件优先 + 根 conftest 冗余次选**）：
  - a) provider 包新增 pytest 插件模块（如 `provider/testing/pytest_model_request_gate.py`），`pytest_configure` 置 gate=**deny**；`packages/provider/pyproject.toml` 注册 `[project.entry-points.pytest11]`——已安装 venv 内**构造性全局生效**（含 per-package rootdir 直跑 / benchmarks tests），免 9 个 tests 目录 conftest 多点同步。
  - b) 顶层 `octoagent/conftest.py` 同置 deny 作**冗余布线**（幂等）——覆盖「worktree PYTHONPATH 锁模式下 entry point 未注册进共享 venv」的窗口（禁 uv sync 铁律的副作用）。
  - c) **两处布线均防御式 import**（`try/except ImportError → no-op + 注释`）：pre-commit hook 在 worktree 收集 worktree conftest 但 import master src（memory `project_precommit_hook_execution_model`），F137 合入 master 前的窗口内 master src 无 gate 模块，hook 不得因此炸。
  - d) 测试断言两种布线各自生效：插件经 `-p` 显式加载可置 deny（worktree 无 metadata 也可验）；根 conftest 布线在标准全量跑下生效。
- **FR-9**（已拍板：**e2e_full marker 驱动开闸**）：`e2e_live/conftest.py` 加 autouse fixture——测试带 `e2e_full` marker 时以 `allow_model_requests()` context 包裹（autouse 同 scope 先于显式 fixture 实例化，早于 `octo_harness_e2e`）；e2e_smoke（不打 LLM）保持 deny。宿主无凭证时照常 SKIP 语义不变。
- **FR-9b**（Fable 复审：**benchmarks 勿误杀**）：OctoBench 真调用走 `octo-bench` argparse CLI（`benchmarks/runner/cli.py:297`，非 pytest 进程）→ pytest 层 deny **构造性不触及**，env 缺省 allow 语义保留；`benchmarks/tests/`（pytest 单测）纳入 A.6 triage——若有用 fake http 直测 dispatch 机器的用例，按 triage 规则补显式 allow，**不**给 benchmarks 目录整体开闸。

### CI（已拍板：B-lite）

- **FR-10**：改写 `feature-007-integration.yml` 为正式确定性层 CI：clean checkout + `uv sync --dev` + `uv run python -m pytest`（照 hook `:83-91` 教训用 `python -m`）跑 L4+L3 确定性层（testpaths 全量、`--ignore` e2e_live 目录），**串行**（Fable 复审：勿 `-n auto`，F083 race；接受 ~20-40min）+ junit artifact；真 LLM（L2）显式不进 per-PR CI（F141 weekly canary 范围）。
- **FR-11**：CI 中 gate=deny 生效（uv sync 后 entry-point 插件构造性激活 + 根 conftest 冗余），任何漏网真调用 FAIL；CI 零 secret 零真网络（free tier）。
- **FR-11b**：CI 加前端 job：setup-node + `npm ci` + `check:complexity` + `vitest run`（FR-12/13 的 CI 侧落点）。

### 前端门禁（已拍板）

- **FR-12**：`check:complexity` 进 **pre-commit + CI** 双闸（node 亚秒级）；pre-commit 段带 `SKIP_FRONTEND_CHECK=1` bypass（照 SKIP_E2E 范式）+ node 缺失降级 SKIP（Constitution #6）。
- **FR-13**：`vitest run` 进 **CI-only**（不进 pre-commit，避免拖慢已 180s 的 commit 环）。
- **FR-14**：3 个 complexity FAIL 处置 = **放宽阈值 + F143 ratchet 注释**（ChatWorkbench.tsx→1250 / useChatStream.ts→700 进 explicitLimits；index.css 3300→4600），**不改这三个文件的代码**（F143 范围）。

### marker

- **FR-15**：`pyproject.toml:70` `e2e_smoke` 描述改为名实相符（「集成层，pre-commit 自动跑，不真打 LLM，≤180s」）；核对 e2e_full/e2e_live 描述。

---

## 4. 验收标准（AC，AC↔test 显式绑定见 plan）

- **AC-1**（硬闸炸漏网）：在 gate=deny 下，构造一个走 provider_direct 且进入 `ProviderClient.call()` 的调用 → 抛 `ModelRequestsNotAllowedError`，**不返回 Echo 结果**、**不触发 auth resolve**（deny 带过期凭证零 auth 端点调用）。绑定：新增 `test_model_request_gate.py`。
- **AC-2**（不误伤合法降级）：gate=allow（或未设 = 生产默认）下，primary 抛普通 `Exception`（如模拟 transport error）→ FallbackManager 仍降级 Echo（`is_fallback=True`），行为与 master 逐字节一致。绑定：`test_fallback.py` 补例 + 现有降级测试 0 regression。
- **AC-3**（两 swallow 站点 re-raise）：gate=deny 下，经 `FallbackManager.call_with_fallback` 与 `LLMService._try_call_with_tools` 两路径的漏网调用，均 propagate `ModelRequestsNotAllowedError`（不 mask 成 Echo/None）。
- **AC-4**（e2e_full opt-in 不误炸）：宿主有真 OAuth 时，e2e_full 真 LLM 测试（gate opt-in allow）正常真跑；宿主无凭证时照常 SKIP。
- **AC-5**（CI 真跑）：修后的 workflow 在 clean checkout（`uv sync --dev`）下确定性层测试真跑并可给绿/红（不再退出码 4）；CI 中 gate=deny 生效（漏网真调用会 FAIL）。本地无法真跑 GitHub Actions → workflow YAML 机械校验 + 首跑预期失败清单进 completion-report（Fable 复审：预算一轮环境敏感 triage，~72 处 sleep 断言慢 runner 可能抖）。
- **AC-6**（前端护栏进闸失效即拦）：故意让某前端文件超阈值 → pre-commit 段 FAIL（本地实测）；vitest 挂 → CI FAIL（workflow 步骤存在性 + 本地 `npm run test` 等价验证）。
- **AC-7**（现 3 FAIL 收敛）：complexity 检查在 master 现状（ChatWorkbench 1204/useChatStream 660/index.css 4477）下 PASS（放宽阈值后本地实测）。
- **AC-8**（marker 名实相符）：`pyproject.toml` marker 描述与 `e2e-testing.md`/conftest 实现一致（人工核对 + grep）。
- **AC-9**（0 regression）：全量回归 vs master a1e4ca15 baseline 0 净回归（实测 baseline：**4846 passed / 11 skipped / 1 xfailed / 1 xpassed，168s**，选择器 = apps+packages+tests、--ignore e2e_live、PYTHONPATH 锁 worktree）；e2e_smoke 8/8。
- **AC-10**（生产零感知）：生产路径（不设 `OCTOAGENT_ALLOW_MODEL_REQUESTS`）gate=allow，Agent 决策/真实 LLM 调用行为无任何变化（#9/#6 守界）。

---

## 5. 非目标（Non-Goals）

- 不消灭真 LLM 测试（M9 全局：只降层不判断力的用例、收敛需判断力的到 weekly）。
- 不建脚本化 LLM harness（F138）/ VCR（F139）/ L1（F140）/ lane 编排（F141）/ coverage 门（F142）。
- 不改 ChatWorkbench/useChatStream 代码结构（F143 专责，F137 只处置阈值）。
- 不引入 real LLM 进 per-PR CI；不建 weekly canary（F141）。

---

## 6. 哲学守界自查（宪法 #6 / #9 / #10）

- **#6 Degrade Gracefully**：硬闸与合法降级**正交**——异常类型区分二者（§0.2 / research.md §C）。生产默认 allow，`FallbackManager`→Echo 真故障语义**零改动**。CI/前端 gate 是 commit/build-time，任一失败不使运行时系统不可用。
- **#9 Agent Autonomy**：硬闸是**测试基础设施开关**，不进任何 Agent 决策路径——不硬编码 model/tool 选择、不加关键词规则替代 LLM。生产从不 deny，Agent 零感知（AC-10 机械验收）。
- **#10 Policy-Driven Access / 认证单入口**：硬闸不碰 front_door / auth_resolver / 权限决策函数，是独立的「真 LLM 网络调用许可」开关，无认证旁路、不分裂权限入口。

---

## 7. ★ 设计岔路拍板决策记录（2026-07-09 拍板 + Fable 5 复审收窄，本节为最终锁定）

### 岔路① 「修 CI」程度 = **B-lite**（用户拍板）

正式 GitHub Actions CI：clean checkout + `uv sync --dev` + 跑**确定性层**（L4 单元 + L3 Echo/hermetic，gate=deny 构造性保证零真调用）on PR/push；真 LLM（L2）**显式不进** per-PR CI（F141 weekly canary 范围）。依据：DeepResearch 共识「真 agent E2E 不能做二元 CI 门」（tau-bench SOTA <50%）+ 真 LLM 进 GH runner 需 secret + ToS/成本顾虑。lane 编排（pr/baseline/release）是 F141 主责，F137 建成「单一 deterministic lane」为 F141 留扩展缝。

**子问题收窄（Fable 复审 ③/⑦）**：
- 范围 = testpaths 全量 `--ignore` e2e_live 目录（e2e_live 内 smoke/full 均依赖宿主凭证，CI 无凭证只会 SKIP，剔除目录更省收集成本 + 更显式；e2e marker 实测只存在于该目录内）。
- **串行首版**（勿 `-n auto`——F083 task_runner race 会炸），接受 GitHub 2-core ~20-40min；F142 xdist_group 落地后减半。
- 首跑预算一轮环境敏感失败 triage（~72 处 sleep 断言慢 runner 可能抖）；后端 job + 前端 job 分开（前端亚分钟级不受后端时长拖累）。

### 岔路② 硬闸形态（主节点按 agent 推荐拍板 + Fable 复审收窄 a/b）

| 决策点 | 锁定 |
|--------|------|
| env 变量名 | `OCTOAGENT_ALLOW_MODEL_REQUESTS`（缺省 allow，生产零感知）|
| 默认值 | **allow**；deny 只由测试布线置（pytest11 插件 + 根 conftest）|
| 植入点 | **`call()` 入口 + `embed()` 入口**（Fable 收窄 a + §0.3 实测：`_dispatch` 太晚，preemptive auth refresh 在其前；`call()` 是唯一 chat 入口 grep 证实无旁路）|
| 异常基类 | **`RuntimeError` 子类**（勿入 `ProviderError` 链防 `except ProviderError` 误吞；语义=配置断言非运行时故障）|
| deny 布线 | **provider 包 pytest11 entry-point 插件优先**（构造性全 venv 生效，免 9 tests 目录多点同步）+ `octoagent/` 根 conftest 冗余次选（覆盖 worktree PYTHONPATH 锁模式 entry point 未注册窗口）；两处均防御式 import（pre-merge hook 窗口）|
| e2e_live 开闸 | **e2e_full marker 驱动**（autouse fixture 按 marker 翻 allow context；e2e_full = 声明真 LLM 意图的单一信号，最少改测试）|
| benchmarks | **勿误杀**：OctoBench 真调用走 argparse CLI 非 pytest → 构造性不受 deny 影响；bench pytest 单测进 A.6 triage 按例处置 |
| 不误伤 Echo | **硬约束**：专用异常 + swallow 站点 re-raise（FR-5/6/7），照 401/403 先例 |

### 岔路③ 前端门禁（主节点按 agent 推荐拍板）

| 决策点 | 锁定 |
|--------|------|
| complexity 落点 | **pre-commit + CI**（node 亚秒级；complexity 正是「失守无人知」最需要快本地闸的项）|
| vitest 落点 | **CI-only**（不拖慢已 180s 的 commit 环）|
| pre-commit bypass | `SKIP_FRONTEND_CHECK=1`（照 SKIP_E2E 范式）+ node 缺失降级 SKIP |
| 现 3 FAIL | **放宽阈值 + F143 ratchet 注释**（ChatWorkbench 1250 / useChatStream 700 / index.css 4600），**不改三文件代码**（F143 范围；gate 价值=挡新增长非逼重构）|

---

## 8. 规模复核（原估 S-M）

**维持 S-M**，偏 M。分解：
- 硬闸（FR-1~9b）：新建 1 小模块 + 插件模块 + 2 处植闸 + 2-3 处 re-raise 守卫 + 2 处布线 + 一组单测 ≈ **S**（代码量小，但**翻 deny 默认后全量 triage 存量假绿**是真工作量 + 双评审最微妙点）。
- CI（FR-10/11/11b）：改写 1 workflow ≈ **S**。
- 前端门禁（FR-12~14）：接线 pre-commit/CI + 放宽 3 阈值 ≈ **S**。
- marker（FR-15）：文案 ≈ **XS**。
- **风险溢价**：硬闸翻 deny 可能抖出未知数量存量假绿（research.md §H.1）——若 >少数几个需逐个 triage/补 opt-in，规模上探 M 上界。

**已知 limitation（归档）**：
- gate 是**进程内**全局：测试若 spawn 子进程发起 LLM 调用，子进程回落 env 缺省（allow）。现状无此形态（e2e 全 in-process harness；MCP 子进程是 node 工具服务不经我们的 provider），与 pydantic-ai 同边界；F141 lane 若引入子进程真跑再收紧。
- entry-point 插件注册进共享 venv 依赖 master 合入后 `uv sync`；worktree PYTHONPATH 锁窗口内由根 conftest 冗余布线兜住（FR-8b/d 显式测试断言两种布线）。

**Phase 拆分见 plan.md**（硬闸独立先做 → CI → 前端 → marker → 双评审 → 文档）。
