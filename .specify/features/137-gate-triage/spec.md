# Feature Specification: F137 门禁止血（Gate Triage）

**Feature ID**: F137
**Slug**: gate-triage
**Milestone**: M9（质量保证体系：四层测试金字塔 + 门禁改造）— **P0**，波次 1（F137 止血 ∥ F138 keystone 并行）
**规模**: **S-M**（复核见 §8）
**Status**: **设计先行草案 v0.1**（研究闭环；spec/plan 待用户拍板 §7 三设计岔路后进入实施）
**Base**: master 8fb1386e / 分支 `feature/137-gate-triage`（worktree `.claude/worktrees/F137-gate-triage/`）
**上游依据**: `CLAUDE.local.md` §M9 + `docs/blueprint/milestones.md` M9 节（line 613-639）+ 本目录 `research.md`（含 file:line 证据）+ `scratchpad/qa_audit_survivors.md`

> ⚠️ 命中「重大架构变更」节点（触碰 CI / provider 分发 `_dispatch` / gateway 测试基础设施）→ spec/plan 大改后回主 session 走 **Codex + Opus 双评审 panel**。本 spec 设计阶段先自查对齐宪法 #6/#9/#10（§6）。

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

### 0.3 ★ 单一分发点 `_dispatch` = 硬闸落点（比 pydantic-ai 7 处便宜）

- `provider_client.py:452` `_dispatch` 是唯一 fan-out 点（在 3 transport 分叉前、网络 I/O 前）。pydantic-ai 要植 6-7 处 request 入口；我们**一处 = 全覆盖 chat**。
- `embed()`（`:912`）是第二网络入口（不走 `_dispatch`）→ 推荐同点加闸（2 处 = 全部出口，仍远比 7 处便宜）。§7 岔路②确认。

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
- **漏网真调用必炸**：provider `_dispatch`（+`embed`）加构造性硬闸——测试声明「不打真 LLM」时漏网的真调用**炸**而非被 Echo 静默吞（堵 bench TLS 事故根源），**且不误伤合法降级**。

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
4. **provider 硬闸**：新增 `ModelRequestsNotAllowedError` + 模块级 gate（默认 allow / env `OCTOAGENT_ALLOW_MODEL_REQUESTS`）+ 植 `_dispatch`（+`embed`）入口 + 两 Echo swallow 站点加 re-raise 守卫 + 顶层 conftest 置 deny 默认 + e2e_live ALLOW opt-in。

### OUT（明确不做，归下游）

- 脚本化 LLM harness / L3 决策环覆盖（**F138**）；VCR 录制回放（**F139**）；L1 Playwright（**F140**）；三模式 pr/baseline/release lane + release 强制 live + change-policy 路由（**F141**）；coverage 三重门 / 第三方库语义钉住（**F142**）；ChatWorkbench/useChatStream 代码下沉（**F143**）。
- **不改** front_door / 认证 / Agent 决策环 / provider 真实调用逻辑（硬闸只加「许可检查」不改调用本身）。
- **不做** 真 LLM 进 per-PR CI（DeepResearch 共识：真 agent E2E 不能做二元 CI 门；收敛 weekly canary 是 F141 事）。

---

## 3. 功能需求（FR）

### 硬闸（核心）

- **FR-1**：新增专用异常 `ModelRequestsNotAllowedError`（provider 包，建议继承 `RuntimeError` 或 provider `ProviderError`——§7 岔路②确认基类；message 含「如何 opt-in」指引）。
- **FR-2**：新增模块级 gate（如 `provider/model_request_gate.py`）：`check_model_requests_allowed()`（deny 时 raise FR-1）+ `allow_model_requests(bool)` 上下文管理器/setter + 初始默认从 env `OCTOAGENT_ALLOW_MODEL_REQUESTS` 读（缺省 = allow=True，**生产不受影响**）。
- **FR-3**：`ProviderClient._dispatch`（`provider_client.py:452`）入口第一行调 `check_model_requests_allowed()`（在 auth resolve 之后、transport 分叉之前均可；确保在网络 I/O 前）。
- **FR-4**：`ProviderClient.embed`（`:912`）入口同样调 `check_model_requests_allowed()`（§7 岔路②确认是否纳入；推荐纳入）。
- **FR-5**：`FallbackManager.call_with_fallback`（`fallback.py:72` 之前）加 `except ModelRequestsNotAllowedError: raise` 守卫（**先于** broad `except Exception`），照 401/403 先例（`:74-85`）。
- **FR-6**：`LLMService._try_call_with_tools`（`llm_service.py:456` 之前）加 `except ModelRequestsNotAllowedError: raise`，照 `SkillAuthError`（`:452`）先例。
- **FR-7**：grep sweep 全仓「broad `except Exception` 邻近 Echo / `return None` / fallback」站点，凡会把 provider 失败 mask 成 Echo/假成功的，均加 FR-5/6 同款守卫（防第三处漏网）。
- **FR-8**：顶层 `octoagent/conftest.py` 加 session autouse，将 gate 置 **deny**（测试默认不许真 LLM）；对齐 pydantic-ai `conftest.py:72`。
- **FR-9**：`e2e_live/conftest.py` 加 ALLOW opt-in（真 LLM 测试——e2e_full marker / `octo_harness_e2e` 依赖链——翻 gate=allow）；确保 e2e_full 真 LLM 测试不被 FR-8 误炸。

### CI

- **FR-10**：`feature-007-integration.yml` 的测试步骤（`:40`）改为跑确定性层（§7 岔路①定具体命令/marker 选择）；job 名/触发保持或按岔路①升级为正式 CI。
- **FR-11**：CI 环境显式设 gate=deny（或依赖 FR-8 conftest），使 CI 中任何漏网真调用 FAIL（构造性保证 CI 不烧钱不打网络）。

### 前端门禁

- **FR-12**：`check:complexity` 接进自动闸（§7 岔路③定 pre-commit / CI / 两者）。
- **FR-13**：`vitest run` 接进 CI（§7 岔路③；推荐 CI-only）。
- **FR-14**：处置当前 3 个 complexity FAIL（§7 岔路③：推荐放宽 3 阈值 + F143 ratchet 注释）。

### marker

- **FR-15**：`pyproject.toml:70` `e2e_smoke` 描述改为名实相符（「集成层，pre-commit 自动跑，不真打 LLM，≤180s」）；核对 e2e_full/e2e_live 描述。

---

## 4. 验收标准（AC，AC↔test 显式绑定见 plan）

- **AC-1**（硬闸炸漏网）：在 gate=deny 下，构造一个走 provider_direct 且触发 `_dispatch` 的调用 → 抛 `ModelRequestsNotAllowedError`，**不返回 Echo 结果**。绑定：新增 `test_model_request_gate.py`。
- **AC-2**（不误伤合法降级）：gate=allow（或未设 = 生产默认）下，primary 抛普通 `Exception`（如模拟 transport error）→ FallbackManager 仍降级 Echo（`is_fallback=True`），行为与 master 逐字节一致。绑定：`test_fallback.py` 补例 + 现有降级测试 0 regression。
- **AC-3**（两 swallow 站点 re-raise）：gate=deny 下，经 `FallbackManager.call_with_fallback` 与 `LLMService._try_call_with_tools` 两路径的漏网调用，均 propagate `ModelRequestsNotAllowedError`（不 mask 成 Echo/None）。
- **AC-4**（e2e_full opt-in 不误炸）：宿主有真 OAuth 时，e2e_full 真 LLM 测试（gate opt-in allow）正常真跑；宿主无凭证时照常 SKIP。
- **AC-5**（CI 真跑）：修后的 workflow 在 clean checkout（`uv sync --dev`）下确定性层测试真跑并可给绿/红（不再退出码 4）；CI 中 gate=deny 生效（漏网真调用会 FAIL）。
- **AC-6**（前端护栏进闸失效即拦）：故意让某前端文件超阈值 → 闸 FAIL；vitest 挂 → CI FAIL（按岔路③落点）。
- **AC-7**（现 3 FAIL 收敛）：complexity 检查在 master 现状（ChatWorkbench 1204/useChatStream 660/index.css 4477）下 PASS（按岔路③处置后）。
- **AC-8**（marker 名实相符）：`pyproject.toml` marker 描述与 `e2e-testing.md`/conftest 实现一致（人工核对 + grep）。
- **AC-9**（0 regression）：全量回归 vs master 8fb1386e baseline 0 净回归；e2e_smoke 8/8。
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

## 7. ★ 设计岔路（必须回用户拍板，本 Feature 交付核心）

### 岔路① 「修 CI」到什么程度？

| 选项 | 内容 | 成本 | 收益 | 风险 |
|------|------|------|------|------|
| A 最小止血 | 只把断链步骤指向现存确定性测试子集（如 `pytest -m "not e2e_full and not e2e_live"` 或指定 tests/integration 的 Echo 全栈），保 CI 绿 | 极小（改 1 行 workflow）| 恢复部分 CI 覆盖，不烧钱 | 覆盖仍窄；未来还要重做成正式 CI |
| **B-lite（推荐）** | 换成正式 GitHub Actions CI：clean checkout + `uv sync --dev` + 跑**确定性层**（L4 单元 + L3 Echo/hermetic，gate=deny 保证零真调用）on PR/push；真 LLM（L2）**显式不进**（留 weekly/F141）| 中（写一个像样 workflow，选 marker 子集，验证 4600 测试时长可控）| 4600+ 测试主体重获 CI 底盘，free tier 零 secret 零网络；硬闸使「CI 不打真 LLM」构造性成立；为 F141 lane 留 deterministic 接口 | CI 时长/shared-runner 资源（需选子集 + 可能分 job）；shared-venv 污染在 clean checkout 不复现 |
| C 正式全 lane | 直接上 pr/baseline/release 三模式 + 真 LLM job + OIDC/secret | 大 | 完整 | **越界 F141**（违「严格执行范围」）；真 LLM 进 CI 违 DeepResearch 共识 |

- **推荐 B-lite**。依据：DeepResearch 共识「真 agent E2E 不能做二元 CI 门」（tau-bench SOTA <50%）+ 真 LLM 进 GH runner 需 secret + 有 ToS/成本顾虑（同 benchmark DeepSeek 决策）。CI 范围 = 确定性层；硬闸让「CI 零真调用」从「靠没配 key 侥幸」升级为构造性保证。lane 编排（pr/baseline/release）是 F141 主责，F137 不 front-run，但把 CI 建成「单一 deterministic lane」为 F141 留扩展缝。
- **子问题（请拍板）**：CI 跑**全量 `-m "not e2e_full and not e2e_live"`** 还是更窄子集（如仅 L4 + tests/integration Echo）？是否接受 CI 单 job 跑数千测试的时长，还是要分 job/并行？

### 岔路② `ALLOW_MODEL_REQUESTS` 式硬闸的确切形态

| 决策点 | 选项 | 推荐 |
|--------|------|------|
| env 变量名 | `OCTOAGENT_ALLOW_MODEL_REQUESTS` | **采用**（对齐 pydantic-ai 命名直觉 + `OCTOAGENT_*` 前缀惯例）|
| 默认值 | allow（生产不受影响）vs deny | **allow**（生产从不设 = allow；deny 只在测试 conftest 置，pydantic-ai 同）|
| 植入点 | 仅 `_dispatch` vs `_dispatch`+`embed` | **两者**（embed 是第二网络出口，同 leak 类；2 处仍远比 pydantic-ai 7 处便宜）|
| 异常基类 | `RuntimeError` 子类 vs provider `ProviderError` 子类 | **待拍板**：`RuntimeError` 子类更贴 pydantic-ai（且明确「非可恢复运行时故障，是配置断言」）；`ProviderError` 子类更贴本仓异常体系但可能被现有 `except ProviderError` 意外捕获——**推荐 `RuntimeError` 子类**（避免被 provider 异常处理链吞，语义也更准）|
| e2e_live 开闸 | ①autouse fixture 按 e2e_full marker 翻 allow ②绑定 `real_codex_credential_store`/`octo_harness_e2e` 进 allow context ③显式 `allow_model_requests` fixture 由真 LLM 测试依赖 | **待拍板**：推荐 ①（marker 驱动，最少改测试；e2e_full = 声明真 LLM 意图的单一信号）；②耦合凭证与许可两关注点；③最显式但要改每个真 LLM 测试签名 |
| **不误伤 Echo（关键）** | 专用异常 + 两 swallow 站点 re-raise（FR-5/6/7）| **硬约束，非选项**（§0.2）——照 401/403 先例。请确认接受此约束 |

- **核心请拍板**：异常基类（RuntimeError vs ProviderError）+ e2e_live 开闸机制（marker vs fixture）+ 确认「不误伤 Echo」= 专用异常 + 两站点 re-raise 的路线。

### 岔路③ 前端门禁落点 + 现 3 FAIL 处置

| 决策点 | 选项 | 推荐 |
|--------|------|------|
| complexity 落点 | pre-commit / CI / 两者 | **pre-commit + CI**（node 脚本亚秒级，够便宜进 pre-commit；且 complexity 正是「失守无人知」最需要快本地闸的项）|
| vitest 落点 | pre-commit / CI / 两者 | **CI-only**（28 文件 ~195 例，秒~十几秒级；进 pre-commit 会拖慢已 180s 的 commit 环。放 CI 每 PR 跑不拖 commit）|
| pre-commit bypass | 有 vs 无 | **有**（照 `SKIP_E2E` 范式加 `SKIP_FRONTEND_CHECK=1`；单人仓紧急 commit 需要逃生门）|
| 现 3 FAIL | A 先修代码 vs **B 放宽阈值 + F143 ratchet** | **B**：把 3 阈值放宽到 current+小余量（index.css 4600 / ChatWorkbench 1250 / useChatStream 700）+ 代码注释「F143 会 ratchet 回收」。理由：F143 明确要下沉 ChatWorkbench/useChatStream——F137 改这俩代码**抢 F143 范围**（违「严格执行范围/别画蛇添足」）；gate 的价值是**挡新增长**不是逼重构。cc-haha ratchet 哲学的最小应用 |

- **请拍板**：complexity 是否进 pre-commit（会让 commit 更慢一点点）？vitest CI-only 可否？3 FAIL 走「放宽 + F143 回收」可否（vs 现在就修代码）？

---

## 8. 规模复核（原估 S-M）

**维持 S-M**，偏 M。分解：
- 硬闸（FR-1~9）：新建 1 小模块（~40 行）+ 2 处植闸 + 2-3 处 re-raise 守卫 + 2 处 conftest + 一组单测 ≈ **S**（代码量小，但**翻 deny 默认后全量 triage 存量假绿**是真工作量 + 双评审最微妙点）。
- CI（FR-10/11）：改/写 1 workflow ≈ **S**（岔路①选 B-lite 时需验证时长/子集，稍涨）。
- 前端门禁（FR-12~14）：接线 pre-commit/CI + 放宽 3 阈值 ≈ **S**。
- marker（FR-15）：文案 ≈ **XS**。
- **风险溢价**：硬闸翻 deny 可能抖出未知数量存量假绿（research.md §H.1）——若 >少数几个需逐个 triage/补 opt-in，规模上探 M 上界。

**Phase 拆分见 plan.md**（硬闸独立先做 → CI → 前端 → marker → 双评审 → 文档）。
