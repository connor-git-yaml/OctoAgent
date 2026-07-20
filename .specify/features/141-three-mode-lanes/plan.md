# F141 实施 Plan

> 对应 spec.md（已按 Codex spec 评审修订：D4v2 skip 三分类 / D5v2 coverage source 配置 /
> D1v2 lane PYTHONPATH 锁 / D9 real_llm marker，见 codex-review-spec.md）。零生产代码改动；
> 文件面 = repo-scripts / .githooks / CI workflow / 测试配置与 conftest / 测试治理资产 / 文档。

## Phase A — 门禁脚本四件 + 单测

| # | 文件 | 内容 |
|---|------|------|
| A1 | `repo-scripts/check-quarantine.py`（新） | stdlib-only；六字段 schema + id/path 去重 + 日期格式校验；`--enforce-review-date [--as-of YYYY-MM-DD]` 过期 exit 1；`--manifest` 可指路径（默认 `octoagent/tests/quarantine.json`） |
| A2 | `repo-scripts/check-attestation.py`（新） | 需 PyYAML（venv 内跑）；提取 `attestation-checklist.md` 第一个 ```yaml block；schema 校验（7 字段 + id 唯一）；`--require-signed [--attest-max-age N] [--as-of]`：非 optional 且 frequency=release 项 last_attested 非 null 且未超龄，否则 exit 1 |
| A3 | `repo-scripts/check-changed-lines-coverage.py`（新） | stdlib-only；`--lcov <path> --base <sha> [--min-percent 90] [--repo-root]`；git diff 解析 + lcov 解析 + 交集判定（spec D5 全规则含 `[cov-exempt]`）；输出未覆盖 文件:行号 清单 |
| A4 | `octoagent/tests/quarantine.json`（新） | `{"quarantined": []}` 初始空 manifest |
| A5 | `octoagent/tests/gate/test_lane_scripts.py`（新，可拆多文件） | importlib 按路径加载 repo-scripts 模块；覆盖 AC-2/AC-3(lane 判定部分)/AC-4 全部单测 |

## Phase B — quarantine 接线（conftest + e2e_live 收窄 + CI 桥删除）

| # | 文件 | 内容 |
|---|------|------|
| B1 | `octoagent/conftest.py`（改） | `pytest_collection_modifyitems`：读 `tests/quarantine.json`（schema 校验失败 → 硬错），命中 nodeid 前缀的 item 加 `flaky(reruns=1)`；文件缺失 → 硬错（治理资产必须在）；过期**不**在 conftest 拦（本地迭代不炸，gate 层拦——cc-haha 同款分工） |
| B2 | `octoagent/apps/gateway/tests/e2e_live/conftest.py`（改） | T-P2-14 blanket 收窄：仅 e2e_full 加 flaky（docstring 改为 live 变异性政策表述）；e2e_smoke 移出 |
| B3 | CI backend job（改） | 删 `--reruns 1 --reruns-delay 2` 及其注释段 |

## Phase C — lane.py 编排器

| # | 文件 | 内容 |
|---|------|------|
| C1 | `repo-scripts/lane.py`（新） | LANES 注册表（spec §3 全表）+ 模式过滤 + 顺序执行 + release 强制语义（SKIP_E2E 无效告警 / --skip 拒 live / live lane passed≥1 且 unexpected_skip=0【junit skip reason 三分类，D4v2】/ service attest JSON status 解析 / attestation --require-signed）+ `--dry-run` + 报告写 `~/.octoagent/logs/lane/<mode>-<ts>.json` + stdout 摘要表；pytest 子进程用 `uv run --no-sync python -m pytest`（cwd=octoagent，PYTHONNOUSERSITE=1 + **PYTHONPATH 锁自身 repo 树 9 个 src 目录**，D1v2/Codex M2） |
| C2 | `octoagent/tests/gate/test_lane_orchestrator.py`（新） | lane 组合按模式过滤正确；release 语义 AC-3 各条含 skip 三分类（用 fake runner 注入，不真跑子进程） |
| C3 | `real_llm` marker（D9）：pyproject 注册 + pytestmark 加 `test_e2e_smoke_real_llm.py` / `test_e2e_mcp_skill_pipeline.py`（先 grep 复核真打面没有第三个文件） | |

## Phase D — pr lane（hook 改造：change-policy + scripted）

| # | 文件 | 内容 |
|---|------|------|
| D1 | `.githooks/pre-commit`（改） | ①`git diff --cached --name-only` 路由：docs-only（全部 ∈ docs/**、*.md、.specify/**）→ 跳 e2e + frontend（打印 fastpath 行）；staged 含 quarantine.json → 跑 check-quarantine；含 attestation-checklist.md → 跑 check-attestation 解析 ②missing-test 警告（生产 src 无伴随测试 → WARNING 不阻断）③pytest 表达式 `-m e2e_smoke` → `-m "e2e_smoke or e2e_scripted"` ④SKIP_E2E 语义不变 |

## Phase E — CI backend job 全量改造

| # | 内容 |
|---|------|
| E1 | checkout `fetch-depth: 0`（changed-lines 需历史） |
| E2 | governance 步：check-quarantine（--enforce-review-date）+ check-attestation（解析校验，uv sync 后） |
| E3 | 主跑加裸 `--cov --cov-report=`（source 走 config，D5v2）；e2e_scripted 步 `apps/gateway/tests/e2e_live -m e2e_scripted --cov --cov-append --cov-report=lcov:coverage.lcov` + 独立 junit |
| E4 | changed-lines 门步：base 决议（PR base.sha / push before / 全零回退 merge-base）+ 跑 A3 脚本；exempt 时大声记录 HEAD sha+subject |
| E5 | `octoagent/pyproject.toml`：`[tool.coverage.run] relative_files = true + source 9 个 src 目录`（D5v2）；注册 `real_llm` marker（D9）；e2e_scripted marker 文案更新 |

## Phase F — tests/AGENTS.md + 文档收敛

| # | 文件 | 内容 |
|---|------|------|
| F1 | `octoagent/tests/AGENTS.md`（新） | spec 件5 全部 8 节 |
| F2 | `.agent-config/shared.md`（改）+ `sync-agent-config.sh` 再生 CLAUDE.md/AGENTS.md | 测试行加指针一句 |
| F3 | `docs/blueprint/testing-strategy.md`（改） | §13.1 lane 📋→✅ + 新增 §13.13 三模式 lane 小节（表格指针到 tests/AGENTS.md，不复制） |
| F4 | `docs/blueprint/milestones.md`（改） | F141 行 ✅ + 一句交付摘要 |
| F5 | `docs/codebase-architecture/e2e-testing.md`（改，若有 pre-commit 描述） | hook 跑集合更新为 smoke+scripted |

## Phase G — 终门验证

1. 全量回归（PYTHONPATH 锁本 worktree + `uv run --project octoagent --no-sync python -m pytest`）
   0 regression vs master d22378b8；
2. e2e_smoke 8/8 + `-m "e2e_smoke or e2e_scripted"` 全绿（pr lane 真跑证据 = 本 Feature commits）；
3. `lane.py baseline` 真跑（=上面全量的编排入口重放或直接以全量结果作证——避免双跑 2×全量，
   以 lane.py 驱动一次为准）；
4. `lane.py release --dry-run` 彩排 + release live 半边执行指引写进归总报告；
5. 合成 lcov 本地演算 changed-lines 门（对 F141 自身 diff 跑一遍脚本，验证「0 新增可执行行 → PASS」
   的首跑预期）。

## Review 节点

- spec/plan commit 后：`codex review --base origin/master`（spec 评审）→ 0 HIGH 再实施；
- 实施完、终门过后：Codex final（挑战四点：release 真堵死 SKIP_E2E？quarantine 过期真 FAIL？
  changed-lines 真机械？AGENTS.md 与实况零漂移？）+ Opus 自审 → 0 HIGH；
- 全程不 push origin。

## 风险与对策

| 风险 | 对策 |
|------|------|
| CI 删 --reruns 后 sleep 断言抖红 | 预期内；抖一条入册一条（quarantine 机制生效方式），报告写明 |
| --cov 拖慢 CI backend（预计 +20~50%） | 60min 兜底富余；报告记录首跑时长供观察 |
| lcov 路径与 git 路径错位 | `relative_files = true` + 脚本内前缀归一（octoagent/ 前缀补齐）+ 单测钉住 |
| hook 在其它 worktree 收集本 conftest（venv 漂移窗口） | quarantine.json 按 conftest `__file__` 相对解析（数据文件非 import，无 venv 耦合）；防御式：读不到 → 硬错信息带路径 |
| e2e_scripted 在 CI 环境有隐性宿主依赖 | F138 AC-8 设计为 CI-runnable（空凭证 store + bomb 防御）；若首跑翻车按 F137 先例 triage |
