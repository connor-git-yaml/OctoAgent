# F137 门禁止血 — Completion Report

**Feature**: F137 / `gate-triage`（M9 P0 波次 1，∥ F138）
**分支**: `feature/137-gate-triage`（worktree，rebase 自 origin/master a1e4ca15）
**状态**: 实施完成，双评审 0 HIGH 残留，**未 push——等用户拍板**
**日期**: 2026-07-11

---

## 1. 每 Phase 实际 vs 计划

| Phase | 计划 | 实际 | 偏离 |
|-------|------|------|------|
| 0 拍板收窄 | 收窄 spec 锁三岔路 + Fable 三收窄注 | ✅ spec v1.0 + Codex 收窄评审 2 P2 闭环（落点表述统一 / ImportError 策略分置）| **闸点从 `_dispatch` 实测修正为 `call()`+`embed()` 入口**（见 §3）|
| A 硬闸 | gate 模块 + 植闸 + swallow 守卫 + 布线 + triage | ✅ 全部 + AC-1~4 机械验收 | sweep 范围比计划大：多守卫 2 站点（skills runner + memu_bridge 两级）；顺带修 1 个真潜伏 bug（§5）|
| B CI | B-lite 双 job | ✅ workflow 改写（名 `ci`）| +`--reruns 1` 过渡桥（Codex P1-2）；vitest 加 6 文件 --exclude（§6）|
| C 前端 | complexity 进 pre-commit+CI / vitest CI-only / 3 阈值放宽 | ✅ 全部 + hook 四分支实测 | **新发现**：master 前端 vitest 存量 11 failed（§6）|
| D marker | 描述文案修正 | ✅ e2e_smoke/e2e_full 双向修正 + F137 gate 语义注 | 无 |
| E 双评审 | Codex + Opus 式自审 0 HIGH | ✅ Codex 3 轮（2 P2 + 2 P1 + 2 P2）全闭环；自审全过（§7）| 无 |
| F 文档 | living-docs 漂移闸 | ✅ testing-strategy §13.1 / e2e-testing DI 勘误 + F137 gate 节 / milestones F137 ✅ | 无 |

## 2. 交付物清单

**新建**：
- `octoagent/packages/provider/src/octoagent/provider/model_request_gate.py` — gate 模块（env 缺省 allow / `ModelRequestsNotAllowedError(RuntimeError)` / context manager / `apply_test_default_deny` 显式-env-优先布线入口）
- `octoagent/packages/provider/src/octoagent/provider/testing/{__init__,pytest_model_request_gate}.py` — pytest11 插件（deny 主布线）
- 测试 4 文件（37 用例）：`packages/provider/tests/test_model_request_gate.py`（27）/ `apps/gateway/tests/test_llm_service_model_request_gate.py`（2）/ `apps/gateway/tests/test_memu_bridge_model_request_gate.py`（2）/ `packages/skills/tests/test_runner_model_request_gate.py`（2）+ provider tests conftest 的 `allow_model_requests_for_dispatch_tests` fixture
- `.specify/features/137-gate-triage/completion-report.md`（本文件）

**修改**：
- 植闸：`provider_client.py`（`call()`/`embed()` 入口第一行）
- swallow 守卫（5 站点）：`fallback.py:73`（call_with_fallback）/ `llm_service.py`（`_try_call_with_tools` 主路径 + 降级重试路径两处）/ `skills/runner.py:181`（模型调用 retry 链前）/ `builtin_memu_bridge.py`（`_fetch_embeddings` + `_try_embed_query` 两级）
- 布线：`packages/provider/pyproject.toml`（pytest11 entry point）/ `octoagent/conftest.py`（冗余布线，防御式 import）/ `e2e_live/conftest.py`（e2e_full marker opt-in autouse fixture）
- 5 个 `test_provider_client_*.py` 加 pytestmark 意图声明（triage 类②）
- CI：`.github/workflows/feature-007-integration.yml` 全量改写
- 前端：`.githooks/pre-commit`（complexity 段）/ `repo-scripts/check-frontend-complexity.mjs`（3 阈值 + ratchet 注释）
- `octoagent/pyproject.toml`（markers）/ `octoagent/.gitignore`（test-results/）
- 文档：`docs/blueprint/testing-strategy.md` / `docs/codebase-architecture/e2e-testing.md` / `docs/blueprint/milestones.md`

## 3. auth-refresh 时机实测结论（闸点修正依据，Fable 复审收窄注 a）

- `_dispatch_with_auth_refresh`（`provider_client.py:404-406`）在进 `_dispatch` **之前**先 `await auth_resolver.resolve()`。
- `OAuthResolver.resolve()` 是**主动 preemptive 刷新**（协议 docstring「preemptive 检查 + 必要时刷新」+「is_expired() 5 分钟 preemptive buffer」→ `TokenRefreshCoordinator.refresh_if_needed`）——token 进 5 分钟过期窗即打真 OAuth token 端点。另有反应式 401/403 force_refresh 路径。
- **结论**：闸植 `_dispatch` 太晚（deny 带过期凭证仍会打真 auth 端点）；已改植 **`call()` 入口第一行**（grep 证实 `_dispatch`/`_dispatch_with_auth_refresh` 仅被 `call()` 链调用，无生产旁路）+ `embed()` 入口第一行（`:942` 同有 resolve）。AC-1 以「mock resolver 计数 == 0」机械锁定。

## 4. A.6 gate=deny 全量 triage 清单

翻 deny 跑全量（4877 收集）：**45 失败，全部为类②「合法直测 dispatch 机器」**，集中 5 个文件：

| 文件 | 数 | 处置 |
|------|----|------|
| test_provider_client_anthropic.py | 8 | pytestmark usefixtures 意图声明放行 |
| test_provider_client_chat.py | 7 | 同上 |
| test_provider_client_responses.py | 9 | 同上 |
| test_provider_client_tool_choice.py | 12 | 同上 |
| test_provider_client_v1_url.py | 9 | 同上 |

- **类①（真假绿 production leak）：0** / **类③（真 LLM 测试漏 opt-in）：0**——现状套件（除 e2e_live）无一测试真正到达 `ProviderClient.call()`；硬闸把「靠没配 key 侥幸」升级为构造性保证（预防性价值，与 spec 定位一致）。
- **不做包级静默放行**：provider 包其余测试保持 deny；放行按文件显式声明。
- **benchmarks**：`octo-bench` CLI 非 pytest → 构造性不受影响；`benchmarks/tests` 插件开/关对照跑结果一致（6 失败均为 tau-bench 手装依赖缺失，与闸无关，两种模式 350 passed 相同）。
- **e2e_smoke 8/8 在 deny 下通过**（worktree src）——smoke「不打 LLM」首次获得构造性证明。
- **e2e_full 真跑 opt-in 实证**（AC-4）：宿主 OAuth 下 `test_domain_1_real_llm_basic_tool_call` 25.4s 真打 GPT-5.5 PASS。

### A.4 swallow 站点 sweep 完整清单

| 站点 | 判定 | 处置 |
|------|------|------|
| `fallback.py:73` except Exception→Echo | 假成功 mask | ✅ re-raise 守卫（照 401/403 先例）|
| `llm_service.py` `_try_call_with_tools` except Exception→return None | 假成功 mask | ✅ re-raise（照 SkillAuthError 先例）|
| `llm_service.py` 降级重试 except Exception→pass | 信号掩埋 | ✅ re-raise（对称 SkillAuthError）|
| `skills/runner.py:181` 模型调用 except Exception→retry/backoff/REPEAT_ERROR | 信号掩埋+拖时间 | ✅ re-raise 先行 |
| `builtin_memu_bridge._fetch_embeddings` except→None | embedding 假绿 | ✅ re-raise |
| `builtin_memu_bridge._try_embed_query` except→None（上一级）| 同上（Codex P2-1 抓漏）| ✅ re-raise |
| `router_message_adapter.py:76` | log 后 re-raise | 已安全，无需改 |
| `llm_service.py:696`（tool_search JSON 解析）/ `:903`（pipeline 列表）| try 块无 provider 调用 | 不涉闸 |
| `session_memory_extractor` / `context_compaction` / `daily_routine` 后台 broad catch | **保留优雅降级（by design）**| 后台 best-effort 路径产出 skip/降级而非假成功；断言路径上游已全守卫，前台漏网必炸。边界判定归档 |
| dx CLI / auth 内部 broad catch | 生产 CLI UX 处理，非 gate 传播路径 | 不涉闸 |

## 5. 顺带抓出的真 bug（2 个）

1. **`builtin_memu_bridge._fetch_embeddings` 四处 `log.warning` 引用不存在的全局 `log`**（模块只定义 `_log`）——runtime 取证 `'log' in vars(module) == False`。四条降级分支（router 未注入/resolve 失败/transport 不支持/embed 失败）一触发即 `NameError`，把优雅降级砸成崩溃（Constitution #6 违反；与 F107 抓的 `_log` F821 同族，ruff F821 未报是因函数体引用「可能延迟定义的全局」不被静态判死）。已改 `_log`（production 行为变更，严格恢复性）。
2. **master 前端 vitest 存量 11 failed / 6 文件**（从未进闸的漂移欠账；详见 §6）。

## 6. AC-6 偏离归档：前端 vitest 存量红

- 接线时发现（`npm ci && npm run test`）：**11 failed / 184 passed**，6 文件（App / AgentCenter / ChatWorkbench / MarkdownContent / HomePage / MemoryPage）。抽查证实为测试-组件真漂移（断言文案/结构过时；HomePage「切换工作上下文」控件缺失**疑似真 UI 回归**），非环境抖动。
- 处置（AC-6 显式偏离）：CI vitest 保持**阻断**，但对 6 个存量红文件 `--exclude` 记欠账（注释说明）——其余 22 文件 118 用例**立即获得阻断保护**（本地实测全绿）；修复归独立 fix task（已派 chip `task_07e4e8c3`）/ F143 对齐。裸阻断会让新 CI 落地即常红（F137 治的病本身），c-o-e 则对绿文件也失去保护——exclude 是两者间保真解。

## 7. 双评审闭环表

**Codex（`codex review --base origin/master`，3 轮）**：

| 轮 | Finding | 级 | 处置 |
|----|---------|----|------|
| 收窄评审 | research §B.1 残留 `_dispatch` 落点旧表述 | P2 | ✅ 标注历史方案被 §I.1 推翻，全制品统一 |
| 收窄评审 | FR-8c 插件/conftest ImportError 策略矛盾 | P2 | ✅ 策略分置：仅根 conftest 防御式；插件 strict |
| Final #1 | pytester `-p 模块` 在已装 entry point 的 venv 双注册 ValueError（CI 稳定卡死）| **P1** | ✅ `-p no:<entry点名>` 前置阻断，两环境等价单注册 |
| Final #1 | 已知 timing race（~72 处 sleep 断言）进阻断 CI 会间歇红 | **P1** | ✅ CI lane 加 `--reruns 1 --reruns-delay 2` 显式过渡桥（junit 可见 rerun；本地不加；F142 治本/F141 quarantine 后删）|
| Final #2 | `_try_embed_query` 上层 broad catch 吞 embed 闸异常→FTS-only 假绿 | P2 | ✅ 同款 re-raise + 2 测试锁定两级贯通 |
| Final #2 | 布线无条件 deny 使公开 env opt-in（通道③）失效 | P2 | ✅ `apply_test_default_deny`：env 未设→deny；显式 env 优先 + 4 测试 |
| Final #3 | entry point 经 provider `__init__` 反向拉 gateway——仅装 octoagent-provider（无 gateway）的 venv 里 pytest 启动即 ModuleNotFoundError | P2 | ✅ **根治**：删除 provider `__init__:47` 对 gateway 的 vestigial 兼容 re-export（`load_project_dotenv`，Feature 003 时代产物，grep 证实顶层 re-export 零消费者——main.py 与 provider/dx 均走 gateway 直接路径）+ `__all__` 同删。gateway-less venv 以 meta_path 阻断器模拟复现：修后插件 import + `pytest_configure` deny 全链路 OK（1.03s）。顺带消除一处「provider 包依赖 gateway 应用」的倒置依赖坏味道 |

**0 HIGH / 0 P1 残留**；全部 finding 接受修复（无带理由拒绝项）。第 4 轮确认见下。

**Opus 式对抗自审（宪法 + AC 机械验收）**：
- **#6**：生产（env 未设）gate 恒 allow → 植闸=一次布尔判断，`FallbackManager→Echo` 真故障降级语义逐字节不变；全部守卫只捕获生产不可能出现的异常类型（构造性死代码）。唯一生产行为变更 = `log`→`_log` 修复（严格恢复 #6）。CI/前端闸均为 commit/build-time。✓
- **#9**：gate 不进任何 Agent 决策路径（不改 model/tool 选择、无关键词规则）；机械取证：生产 src 对 `set_allow_model_requests`/`apply_test_default_deny` **零调用**（仅测试布线/测试）。✓
- **#10**：auth 面（`auth/` 目录 / `auth_resolver.py` / `frontdoor_auth.py`）`git diff --stat` **零改动**；闸点在 auth resolve 之前短路整个调用，未新增任何 auth 旁路。✓
- **AC 1-10 逐条**：AC-1✓（resolver 计数 0）AC-2✓（普通异常仍 Echo + 全量 0 回归）AC-3✓（4 路径 propagate 测试）AC-4✓（真 OAuth e2e_full 25s PASS / 无凭证 SKIP 语义未动）AC-5✓（YAML 机械校验 + 后端/前端命令本地跑通【Codex 复核证言】；真 GH 跑待合入）AC-6✓（hook 四分支实测；vitest 见 §6 偏离）AC-7✓（放宽后 PASS）AC-8✓（marker↔实现↔文档三向一致）AC-9✓（终值见 §8）AC-10✓（#9 取证同款）。

## 8. 回归与验证汇总

| 项 | 结果 |
|----|------|
| baseline（a1e4ca15，改动前实测）| 4846 passed / 11 skipped / 1 xfailed / 1 xpassed（168s）|
| 终态全量（deny 生效 + 全部 opt-in）| **4883 passed / 11 skipped / 1 xfailed / 1 xpassed = baseline + 37 新增，0 净回归** |
| e2e_smoke（worktree src + deny）| 8/8 PASS（两次：Phase A 后 + Phase D 后）|
| e2e_full opt-in 真跑 | domain_1 真打 GPT-5.5 PASS（25.4s）|
| benchmarks 对照 | 插件开/关一致（350 passed / 6 tau-bench 依赖缺失失败，与闸无关）|
| 前端 | complexity PASS；vitest 排除欠账后 22 文件 118 用例全绿 |
| ruff | 新文件全 clean；改动文件无新增 error（memu_bridge I001/E501 均既有）|

## 9. CI 首跑预期失败清单（给主 session 合入后 triage）

1. **backend job 时长**：本地 M4 Pro 串行 ~3min；GitHub 2-core 预估 20-40min（timeout-minutes: 60 兜底）。
2. **sleep 断言残余抖动**：`--reruns 1` 已兜单次抖动；若同一测试连续两次失败=真问题非 flake（rerun 计数在 junit artifact）。已知最脆弱：`tests/integration/test_f009_worker_runtime_flow.py`（sleep(0.4) 型）。
3. **uv/npm 缓存首跑冷**：首跑 uv sync + npm ci 各 +1-3min，二跑起命中缓存。
4. **entry-point 插件在 CI 生效路径**：clean checkout + uv sync → 插件自动注册 → deny 构造性生效（无需任何 env/secret）。若见 `ModelRequestsNotAllowedError` FAIL = 抓到真漏网（按 §4 triage 三分法处置），不是基础设施故障。
5. **vitest exclude 清单**：6 文件欠账修复后删除 `--exclude`（chip `task_07e4e8c3`）。

## 10. 风险与 handoff 备忘

- **旧 worktree entry-point 遮蔽窗口**（F137 合入 + 主 venv sync 后）：基于 pre-F137 master 的 worktree（如并行中的 **F138**）PYTHONPATH 锁跑 pytest 会因插件模块在其 provider 包缺失而启动期 ImportError（响亮非静默）。**自愈=rebase master**；临时逃生门 `-p no:octoagent_model_request_gate`。已在 research §I.6 归档。
- **共享 venv 重指现象**（本次实证）：pre-commit hook 的 `uv run`（无 `--no-sync`）会把共享 venv editable 重指到发起 commit 的 worktree（本次也观测到 venv 曾被 F138 worktree 重指——双向常态）。本 Feature 收尾时已在主仓 `uv sync --dev` 归一到 master；后续 F137 的 commit 我改用 `SKIP_E2E=1` + 手动 PYTHONPATH 锁跑 smoke 替代（验证等效且不污染并行会话）。
- **skills/runner.py 触碰说明**（F138 地盘毗邻）：仅在模型调用 except 链**最前**插入 4 行 re-raise + 1 条 import（FR-7 sweep 必要站点——否则闸异常被 retry/backoff 拖慢并转 REPEAT_ERROR 掩埋）。未动 harness/model_client 相关结构；与 F138 计划面（octo_harness DI / QueueModelClient 上提 / skills.testing 子包）无文件级冲突——但 F138 若也建 `octoagent/provider/testing` 之外的 `skills/testing` 子包命名对称，合并时零冲突。
- **F141/F142 回收点**：CI `--reruns 1`（F142 sleep→轮询治本 / F141 quarantine 后删）；vitest `--exclude`（欠账清零后删）；前端 3 阈值（F143 后 ratchet 回收）。
