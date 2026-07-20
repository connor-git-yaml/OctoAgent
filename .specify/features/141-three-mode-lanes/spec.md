# F141 三模式 lane 门禁 — 收窄 Spec

> M9 波④。上游依据：`docs/blueprint/milestones.md` M9 F141 行 + Fable 5 复审调整②⑤ +
> 验证吸收原则段；`CLAUDE.local.md` §M9 竞品采纳明细（三模式 lane / flaky quarantine /
> 分级验证路由 / coverage 三重门）；F144 handoff
> `.specify/features/144-attestation-absorb/handoff-to-F141.md`（attest --json 契约 + 4 语义坑）；
> F142 completion-report §F141 回收点（CI `--reruns 1` 过渡桥删除 + budget guard 纳入 CI）。
> 竞品参考：cc-haha `scripts/quality-gate/{modes,quarantine,coverage}.ts` + `scripts/pr/change-policy.ts`
> + `AGENTS.md` Verification Routing（`_references/opensource/claude-code/`）。

## 0. 一句话

把散落的门禁资产（pre-commit e2e_smoke / CI B-lite / `octo attest` 探针 / attestation 清单 /
marker 体系）编排成 **pr / baseline / release 三模式 lane**，release 强制 live（skip 即 FAIL），
补 flaky quarantine manifest、change-policy 路由、changed-lines coverage 门与 `tests/AGENTS.md`
机器可读测试契约——**零生产代码改动**，全部落在 repo-scripts / .githooks / CI / 测试配置 / 文档层。

## 1. 范围：6 件「已有 vs 补」

### 件1 三模式 lane 编排

| 已有 | 补 |
|------|-----|
| pr 半边：`.githooks/pre-commit`（sync-check + frontend complexity + e2e_smoke 180s watchdog + `SKIP_E2E` bypass） | `repo-scripts/lane.py` 三模式编排器（LANES 注册表：id/modes/live/命令，仿 cc-haha modes.ts） |
| baseline 半边：本地全量回归惯例（`uv run --no-sync python -m pytest` 全 testpaths，含 e2e_live）但无编排入口、无记录 | baseline 模式 = quarantine 校验 + backend 全量 + frontend complexity+vitest + L1 可选（`--with-l1`）；run 报告落 `~/.octoagent/logs/lane/` |
| release 半边：`octo attest service --json`（F144）+ attestation-checklist.md（机器可读 YAML）+ e2e_full 真 LLM 套件——**三者从未被编排**，真机验证反复推迟 | release 模式 = 确定性全量（`-m "not e2e_full"`）+ **live-e2e-full lane（skip 即 FAIL）** + `attest service` + attestation 签署核对；`SKIP_E2E` 在 release 无效 |
| CI：`.github/workflows/feature-007-integration.yml` B-lite（backend-deterministic + frontend + l1-playwright） | CI 即「push 触发的 deterministic lane」定位不变；backend job 补 e2e_scripted 步（回收 F142 遗留：budget guard 等 16 个 CI-runnable 测试现被 `--ignore=e2e_live` 排除） |

### 件2 flaky quarantine manifest

| 已有 | 补 |
|------|-----|
| e2e_live conftest T-P2-14 blanket flaky（e2e_smoke+e2e_full 全部自动 `flaky(reruns=1)`） | blanket 收窄为 **e2e_full-only**（真 LLM 固有变异性政策，非 flake 掩盖）；e2e_smoke（确定性）移出 blanket |
| CI backend `--reruns 1 --reruns-delay 2`（F142 显式过渡桥，留言「F141 quarantine 后删」） | **删除**该参数（F142 mandate） |
| 已知 flaky 只活在 pyproject F083 注释散文 + CLAUDE.md | `octoagent/tests/quarantine.json` 六字段 manifest（id/path/reason/owner/review_after/exit_criteria）+ `repo-scripts/check-quarantine.py`（schema/重复/日期校验 + `--enforce-review-date` 过期即 FAIL）+ `octoagent/conftest.py` 加载 manifest 给命中 nodeid 加 `flaky(reruns=1)` |
| F142 两个「永久 CI 豁免」性能断言（`test_finalize_result_offload.py` / `test_threat_scanner_boundary.py` 的 `skipif CI`） | **保 skipif 不入册**（决策 D3，理由见 §2） |

### 件3 change-policy 路径路由（保守两条）

| 已有 | 补 |
|------|-----|
| pre-commit 扁平全量：任何 staged 内容都跑完整 e2e_smoke（纯 docs 改动也等 ~40-60s） | 「纯 docs 提速」：staged 全部 ∈ {`docs/**`, `**/*.md`, `.specify/**`} → 跳过 e2e 与 frontend 检查（sync-check 恒跑——CLAUDE.md/AGENTS.md 本身是 .md）；staged 含 `octoagent/tests/quarantine.json` → 附跑 check-quarantine；含 `attestation-checklist.md` → 附跑 check-attestation 解析校验 |
| 无「生产代码改动缺伴随测试」信号 | 「missing-test 警告」：staged 含生产 src（`octoagent/{packages/*/src,apps/gateway/src,frontend/src}`，排除测试文件）且无任何 staged 测试文件 → 打印 WARNING，**不阻断**（保守起步，cc-haha 是 label 阻塞制——我们先只做信号） |
| — | **不做**复杂 area 矩阵（frontend→vitest 子集等），显式范围外 |

### 件4 changed-lines coverage 门（Fable 复审从 F142 挪入）

| 已有 | 补 |
|------|-----|
| pytest-cov 在 dev 依赖但从未接线（审计 [missing] 实证：全仓 0 处 --cov / [tool.coverage] / fail_under） | CI backend job 接 `--cov=octoagent --cov-report=lcov` + `octoagent/pyproject.toml` 加 `[tool.coverage.run] relative_files = true`；`repo-scripts/check-changed-lines-coverage.py` 机械计算 `git diff --unified=0 <base>...HEAD` 新增行 ∩ lcov DA 记录，**新增可执行行覆盖 ≥90% 否则 exit 1**（cc-haha coverage.ts `evaluateChangedLineCoverage` 范式） |
| escape hatch 先例：SKIP_E2E env（本地）| CI 侧 escape hatch = HEAD commit message 含 `[cov-exempt]` 标记（env 不随 push 传播，commit message 是唯一随 push 的信道） |
| — | 存量不背债：只算本次 diff 新增行；范围收敛 `octoagent/{packages/*/src,apps/gateway/src}/**/*.py`（frontend coverage 是 F143 之后的事，本 Feature 不碰）；scope 底线/棘轮两重门显式 defer（对 4600+ 测试仓引入成本高，changed-lines 单条 ROI 最高——审计拍板） |

### 件5 tests/AGENTS.md 机器可读测试契约

| 已有 | 补 |
|------|-----|
| 教义散在三处：CLAUDE.md（1 行「每模块 unit test」）/ testing-strategy.md §13（策略+历史，F137-F142 已更新）/ 私有 memory（pytest 调用纪律、worktree venv 语义、hook 执行模型）——面向人、无机械判定表 | `octoagent/tests/AGENTS.md`：①四层判定表（这个用例进 L1/L2/L3/L4 的机械规则）②marker 语义表（e2e_smoke/full/live/scripted + gate 开闸行为 + xdist_group）③lane 模式表 ④调用纪律（PYTHONPATH 锁 / 禁 uv sync / `python -m pytest` / hook venv 语义）⑤flaky 三分处置 ⑥红线（禁 sleep 赌窗口→条件等待/受控时钟，F142 治欠账范式）⑦验证路由表（feedback_verification_right_sizing 机械化，仿 cc-haha Verification Routing）⑧coverage 门与 escape hatch |
| — | **修漂移不复制粘贴**：testing-strategy.md §13.1 lane 行 📋→✅ + 指针；`.agent-config/shared.md` 测试行加一句指针后重新生成 CLAUDE.md/AGENTS.md（sync 脚本流程）；pyproject e2e_scripted marker 文案「F141 归入 pr lane」改为已兑现表述 |

### 件6 `--reruns 1` 过渡桥处置

| 已有 | 补 |
|------|-----|
| CI backend `--reruns 1 --reruns-delay 2` + 注释「F141 quarantine manifest 落地后删除本参数」 | **删除**（quarantine.json + conftest 定向 rerun 就位即删；junit rerun 计数信道由 manifest 的显式 reason 替代）。首跑预期：若 ~72 处 sleep 断言残余在慢 runner 抖红 → 逐条入册 quarantine.json（带 review_after + exit_criteria），这是机制按设计运作而非回归 |

## 2. 关键决策（8 条）

### D1 lane 形态 = `repo-scripts/lane.py` 薄脚本（拒绝 `octo lane` 子命令）

- 任务红线「生产代码零改」直接排除 `packages/provider/dx/cli.py` 改动；
- lane 是 **repo 开发态门禁**（消费 .githooks / repo-scripts / CI / .specify 语境），不是产品能力——
  进产品 CLI 会把 repo-only 概念泄漏进用户安装面（`octo e2e` 是历史先例但其消费物在包内，lane 的消费物在包外）；
- 先例：`check-frontend-complexity.mjs`。
- 调用形态：`uv run --project octoagent --no-sync python repo-scripts/lane.py <mode>`（lane.py 需要
  PyYAML 解析 attestation 清单——provider/skills 的传导依赖，venv 恒有；`--no-sync` 遵守 worktree 纪律）。
- venv 漂移防护（Codex M2 修订 v2）：lane.py 调 pytest 时显式 **PYTHONPATH 锁自身 repo 树**
  （`__file__` 推 repo root → 拼 9 个 src 目录 + `PYTHONNOUSERSITE=1`）——PYTHONPATH 先于
  site-packages，共享 venv editable 指向任何 worktree 都不影响被测代码来源；stale venv 缺新依赖
  时 import error 快败，报错提示主仓 `uv sync`。
- 三个校验器独立成脚本（`check-quarantine.py` stdlib-only 供 hook 直调 / `check-attestation.py` /
  `check-changed-lines-coverage.py` stdlib-only 供 CI 直调），lane.py 只做编排——单一职责 + 独立可测。

### D2 attest not_enabled 处置（任务书设计要求）

- **探针三态 × exit code 坑**（F144 handoff §1）：`not_enabled` 与 `pass` 同 exit 0 → lane **必须解析
  `--json` 的 `status` 字段**，不得只看 exit code。
- `attest service` = not_enabled：**恒 FAIL 无 flag**——常驻服务是部署形态前提（F129 / handoff §1
  明示「应视为 WARN/FAIL 级」，取 FAIL：release 跑在部署机上，服务未安装是真阻断）。
- `fail`（exit 1）：恒阻断。
- 恢复预算：`--budget` 透传缝暂不加（handoff §4.4 说 CLI 未暴露；lane 首版直跑默认 90s，慢机器
  需要时是 S 级后补）。

### D3 flaky 三分处置（「哪种形态更诚实」判断）

| 类型 | 处置 | 理由 |
|------|------|------|
| 真 flaky（时序/环境间歇） | `quarantine.json` 六字段入册 + 定向 `flaky(reruns=1)` + **过期即 gate FAIL** | 隔离必须有 owner/退出条件/复查日期，不可能烂尾（cc-haha quarantine.ts 范式） |
| 环境永久不适用（F142 两个绝对时长性能断言） | **保 `skipif CI` 不入册** | quarantine 语义=「临时隔离+复查到期」；这两条是 F142 显式拍板的**永久**豁免（绝对时长阈值 vs 共享 runner 本质不可靠，无 exit criteria）。入册会强制无意义复查续期 churn，把「过期即 FAIL」污染成例行盖章——训练人忽略过期告警，恰是 F124 SC-008 反「狼来了」要防的。skipif + 理由内联在测试文件里，离断言最近、最诚实 |
| 真 LLM 固有变异性（e2e_full） | e2e_live conftest 保留 **e2e_full-only** marker 级 rerun | 这是 live 变异性政策而非 flake 掩盖（GATE_P3_DEVIATION SKIP 是主机制，rerun 是次级缓冲）；e2e_smoke 确定性套件移出 blanket——它 rerun 属掩盖 |

`quarantine.json` **初始为空**：F142 已治根因最热三处（task_runner attach_input / f009 / f131），
现无具体已知 flaky 路径；~72 处 sleep 断言是 diffuse 面，抖红一条入册一条（机制生效方式）。

### D4 release「skip 即 FAIL」语义 = lane 级 + skip 分类制（Codex H2 修订 v2）

- cc-haha `enforceReleaseLiveLanes`：release 模式下 **live lane 整体被 skip → 强转 FAIL**。
- 机械规则：live lane（`pytest -m real_llm`，见 D9）pass 条件 =
  exit 0 **且 passed ≥ 1 且 unexpected_skip = 0**。skip 三分类（解析 junit skipped reason）：
  - `deviation_skip`（reason 含 `GATE_P3_DEVIATION`）：设计内 LLM 未命中，记录不阻断；
  - `manual_gate_skip`（reason 命中人工闸清单，如 `OCTOAGENT_E2E_PERPLEXITY_API_KEY`）：记录不阻断；
  - **`unexpected_skip`（其余一切：凭证缺失 / quota / 环境）：≥1 即 lane FAIL** 并列出 nodeid+reason。
  报告输出四类计数（passed / deviation / manual_gate / unexpected）。
  「passed ≥ 1」底线保留——抓「全 SKIP → exit 0 假绿」；skip 分类抓「1 个确定性 pass 掩护大量
  静默 SKIP」（Codex H2）。
- `SKIP_E2E` 在 release 模式**无效**：lane.py 检测到该 env 时打印「release 模式下 SKIP_E2E 无效」
  并照常执行（不消费）；release 模式同时**拒绝** `--skip` 指向 live / attestation lanes（exit 2）。
- pr/baseline 模式的 lane.py 调用同样不消费 SKIP_E2E（显式调 lane = 显式要跑门禁；SKIP_E2E 仍是
  pre-commit hook 专属逃生门，日常语义不变——红线要求）。

### D5 changed-lines coverage 门机械规则（CI-only；Codex H3 修订 v2）

- 新增行来源：`git diff --unified=0 --no-ext-diff <base>...HEAD` 解析 hunk `+` 行号（纯新增侧）。
- base 决议：PR 事件 = `pull_request.base.sha`；push 事件 = `event.before`，全零/不可解析
  （新分支首推）→ 回退 `merge-base origin/master HEAD`；backend job checkout 需 `fetch-depth: 0`。
- 覆盖数据：`[tool.coverage.run]` 配 `relative_files = true` + **`source` 显式列 9 个 src 目录**
  （`packages/*/src` × 8 + `apps/gateway/src`——目录形态避开 namespace 包按模块名解析的不确定性，
  Codex H3）；CI 用裸 `--cov`（无值 → 走 config source）。SF 路径 = 相对 `octoagent/` 的
  `packages/.../src/...`，脚本补 `octoagent/` 前缀与 git 路径对齐（单测钉住）；主跑 +
  e2e_scripted 步 `--cov-append` 合并。
- 范围：`octoagent/packages/*/src/**/*.py` + `octoagent/apps/gateway/src/**/*.py`（生产代码）；
  测试文件 / repo-scripts / frontend 不计。
- 判定：新增行 ∩ lcov 可执行行（DA 记录），命中率 < 90% → exit 1 并列出未覆盖文件:行号；
  新文件无任何 lcov 记录 → 该文件新增行全按 0 覆盖计（抓「加了模块没有任何测试 import」）；
  新增可执行行数 = 0 → PASS（docs/测试/脚本改动天然过门）。
- escape hatch：HEAD commit message 含 `[cov-exempt]` → 门以 exempt 状态 PASS 并**大声记录**
  （HEAD sha + subject 进 CI 日志；照 SKIP_E2E 先例：可 bypass、有痕迹）。Codex M1「空提交整推
  豁免」拒绝带理由：单人仓威胁模型是「忘」不是对抗性开发者，per-commit trailer 不增防护只加摩擦
  （见 codex-review-spec.md）。
- 该门**只进 CI 不进 pre-commit**（红线：正常 commit 不变慢）。

### D6 pr lane 纳入 e2e_scripted（实测门槛内）

- F138 pyproject marker 文案预留「F141 归入 pr lane」；实测本 worktree
  `-m "e2e_smoke or e2e_scripted"` = **24 passed + 1 skipped / 8.1s**（wall ~10.6s 含 uv 启动），
  远低于 180s watchdog 预算 → hook pytest 表达式改 `-m "e2e_smoke or e2e_scripted"`。
- 价值：决策环前半段（F138 keystone）+ F136 审批全链（F144 gap-1）+ prompt 预算护栏（F142 件2）
  进 commit 时反馈环。

### D7 tests/AGENTS.md 落位 `octoagent/tests/AGENTS.md`

- 与 `quarantine.json` 同目录聚拢成「测试治理资产」；pyproject testpaths 的根在 `octoagent/`，
  该目录是全仓测试的最近公共治理点（各包 tests 分散，不宜多点复制）。
- 根 CLAUDE.md/AGENTS.md 由 `.agent-config` 生成——shared.md 测试行加**一句指针**后跑
  `sync-agent-config.sh` 再生（不绕过 sync 检查）；testing-strategy.md 同样指针化，不复制表格。

### D8 CI backend job 补 e2e_scripted 步（回收 F142 遗留点）

- F142 completion-report §F141 回收点明示：budget guard 在 e2e_live 目录，被 CI
  `--ignore=apps/gateway/tests/e2e_live` 排除，「待 F141 lane 显式纳入 CI」。
- e2e_scripted 16 个测试（decision_loop 4 + write_approval 2 + model_client_di 5 + budget_guard 5）
  全部零真 LLM / 零宿主 OAuth（F138 AC-8 / F144 设计如此）→ backend job 第二步
  `pytest apps/gateway/tests/e2e_live -m e2e_scripted --cov-append`，deny 闸全程在位
  （e2e_scripted 不触发 e2e_full 开闸 fixture）。

### D9 新增 marker `real_llm`——lane 切分不复用 `e2e_full`（Codex H1 修订）

- 实况：`e2e_full` 的 9 个域文件里 7 个是确定性直调（notification_persist / file_workbench /
  ssrf_guard / tool_result_threat_scan / memory_pipeline / delegation_a2a / routine 等），真发起
  LLM 网络调用的只有 `test_e2e_smoke_real_llm.py` + `test_e2e_mcp_skill_pipeline.py` 两个文件
  （审计 L2 finding；实施期 grep 复核）。`e2e_full` 是「gate 开闸声明」不是「真调用事实」。
- 若 release 用 `-m "not e2e_full"` / `-m e2e_full` 切分：确定性 e2e_full 测试被赶出 deterministic
  lane，且它们的确定性 PASS 会满足 live lane `passed>=1` → 假绿（H1×H2 叠加洞）。
- **修法**：pyproject 注册 marker `real_llm`（真打 LLM/外部网络的用例，`e2e_full` 子集），
  pytestmark 加到上述 2 个文件。release deterministic lane = `-m "not real_llm"`；
  live lane = `-m real_llm`。`e2e_full` 原语义（gate 开闸 + `octo e2e full` 套件）不动；确定性
  e2e_full 测试拿到不必要 gate=allow 属 pre-existing sloppiness，超范围，AGENTS.md 记录。

## 3. 三模式 lane 定义（实施基准）

| lane id | pr | baseline | release | live | 命令 / pass 规则 |
|---------|----|----|----|------|-----------------|
| quarantine-governance | ✅ | ✅ | ✅ | — | `check-quarantine.py --enforce-review-date`；过期条目 → FAIL |
| agent-config-sync | ✅ | — | — | — | `sync-agent-config.sh --check`（baseline/release 由 commit 时 hook 已保证） |
| frontend-complexity | ✅ | ✅ | ✅ | — | `node check-frontend-complexity.mjs` |
| backend-smoke-scripted | ✅ | — | — | — | `pytest -m "e2e_smoke or e2e_scripted"`（pr 专属快反馈） |
| backend-full | — | ✅ | — | 半 | 全 testpaths（含 e2e_live；real_llm 凭证在场即真打，SKIP 记录不阻断） |
| backend-deterministic | — | — | ✅ | — | 全 testpaths `-m "not real_llm"`（确定性 e2e_full 测试留在本 lane；避免与 live lane 双跑真 LLM） |
| frontend-vitest | — | ✅ | ✅ | — | `npx vitest run` |
| l1-playwright | — | 可选 | — | — | `--with-l1` 显式开启（本地需 playwright 浏览器；CI 有独立 job 常跑） |
| live-real-llm | — | — | ✅ | ✅ | `pytest -m real_llm`；**exit 0 且 passed ≥ 1 且 unexpected_skip = 0**（D4v2 skip 三分类）；四类计数进报告 |
| attest-service | — | — | ✅ | ✅ | `octo attest service --json`；status=pass 才过；not_enabled → FAIL |
| attestation-signed | — | — | ✅ | — | `check-attestation.py --require-signed`：非 optional 且 frequency=release 项 `last_attested` 非 null 且 ≤ `--attest-max-age`（默认 90 天）否则 FAIL |

- pr lane 的 canonical 执行点仍是 pre-commit hook（含 change-policy 路由）；`lane.py pr` 是同一
  组检查的手动入口（不含 staged-diff 路由——那只在 commit 语境有意义）。
- 报告：每次 run 写 `~/.octoagent/logs/lane/<mode>-<ts>.json`（含 attest JSON 全文——F144 保证
  token 零泄漏可归档）+ stdout 摘要表。
- `--dry-run`：打印 lane 计划 + 只跑 governance/解析类 lane（quarantine / attestation 解析），
  不跑 pytest / 探针——release 的无副作用彩排入口。
- attestation 签署动作本身归人工（编辑 `attestation-checklist.md` 回填日期，Constitution #7——
  lane 只核对不代签）。

## 4. 非目标（显式范围外）

- `octo lane` 产品 CLI 子命令（D1）；scope 底线 / 棘轮两重 coverage 门；frontend coverage；
  复杂 area 路由矩阵；persistence_upgrade 专用 marker（独立候选）；inline-snapshot（F142 defer 待评估）；
  weekly canary 编排（真 LLM 周期跑是 F141 后的运维决定，本 Feature 提供 lane 机制不建 cron）；
  pre-commit hook 的 `uv run` sync 语义改动（已知 venv 重指陷阱，非本 Feature 范围，动它影响所有 worktree）。
- 不碰 F143 地盘（`frontend/src/**`）、不碰 F139 地盘（`packages/provider/tests` cassette/vcr）、
  不动 CI l1-playwright / frontend job 主体。

## 5. 验收标准

- **AC-1**（三模式真跑）：pr = 本 Feature 自身 commit 实证（hook 走 smoke+scripted + change-policy）；
  baseline = `lane.py baseline` 本地全量 0 regression vs master d22378b8；release = `lane.py release
  --dry-run` 彩排 + 各 live lane 判定逻辑单测覆盖（真 live 半边由主 session 按报告指引执行——本
  agent 不真打 LLM）。
- **AC-2**（quarantine 咬人）：单测证 ①六字段缺失/重复 id/坏日期 → 校验 FAIL；②`review_after` 过期
  → `--enforce-review-date` exit 1；③conftest 对命中 path 的 item 加 flaky marker、未命中不加。
- **AC-3**（release 堵逃生门）：单测证 ①live lane pytest 全 SKIP（exit 0 passed=0）→ lane FAIL；
  ②`SKIP_E2E=1` 环境下 release lane 照跑并打印无效警告；③`--skip live-real-llm` 在 release → exit 2；
  ④attest service not_enabled → FAIL；⑤unexpected_skip ≥ 1（如凭证缺失 reason）→ lane FAIL，
  deviation/manual_gate skip 不阻断（D4v2）。
- **AC-4**（changed-lines 机械）：单测用合成 lcov + 合成 diff 证 ①90% 判定含边界（=90 过 / <90 FAIL）；
  ②新文件无 lcov 记录按 0 覆盖；③非可执行行不计；④0 新增可执行行 → PASS；⑤`[cov-exempt]` → exempt PASS。
- **AC-5**（CI 接线）：backend job 无 `--reruns`；有 `--cov` + lcov + e2e_scripted 步 + 门步 +
  governance 步；`fetch-depth: 0`。首跑预期写进归总报告（coverage 门首跑按 F141 自身 diff 计算——
  新增全在 repo-scripts/测试/文档，范围内新增可执行行 ≈ 0 → 门 trivially PASS 属预期）。
- **AC-6**（AGENTS.md 零漂移）：文内 marker 表 / lane 表 / 调用纪律与 pyproject、hook、CI、lane.py
  实况一致（final review 逐条对照）；三处教义源改指针不留复制体。
- **AC-7**（0 regression）：全量回归 vs master baseline 0 failed；e2e_smoke 8/8；组合新增测试全绿。
