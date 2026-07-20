# OctoAgent 测试契约（tests/AGENTS.md）

> **给 coding agent 与人的机器可读判定表**（F141）。测试怎么分层、怎么跑、怎么治 flaky、
> 过什么门——以本文为准；`docs/blueprint/testing-strategy.md` 讲策略与历史，本文讲操作契约。
> **本文不被任何脚本解析**（机器消费的 gate 资产是 `octoagent/tests/quarantine.json` 与
> `docs/codebase-architecture/attestation-checklist.md`，各有校验器）；但 AC/表格与实况的
> 一致性是每个触碰测试体系的 Feature 的 completion gate 检查项。

## 1. 四层判定表（新用例进哪层）

按第一条命中的规则落层：

| 判定 | 层 | 落点 | 机制 |
|------|----|------|------|
| 需要真 LLM 判断力（决策质量/理解力本身是被测物） | **L2 live** | `apps/gateway/tests/e2e_live/`，marker `e2e_full + real_llm` | 真打 LLM；只在 release lane / 手动 `octo e2e full` 跑，**永不进 per-commit/CI** |
| 验证「LLM 决定调哪个工具 → 派发 → 回写」决策环，但不需要真判断力 | **L3 scripted** | `e2e_live/`，marker `e2e_scripted` | `ScriptedModelClient` 脚本脑（F138）经 `OctoHarness(model_client=...)` DI；零真 LLM / 零宿主 OAuth，CI-runnable |
| 全栈链路（bootstrap/API/事件链/存储）但不触 LLM 决策 | **L3 确定性 e2e** | `e2e_live/`（marker `e2e_smoke`，harness 集成层）或 `tests/integration/`（Echo 全栈） | DI stub / EchoMessageAdapter；gate=deny 兜底漏网真调用必炸 |
| 浏览器才有的语义（真 EventSource/storage/渲染/滚动） | **L1 UI E2E** | `frontend/e2e/`（Playwright） | UI 只做输入通道，断言走 REST 事件链 + 文件系统（F140） |
| 其余一切（纯逻辑/单模块/store/服务级） | **L4 单元/服务** | `packages/*/tests/` 或 `apps/gateway/tests/` 顶层 | tmp SQLite + DI fake；第三方库包裹层须配「真库 API 签名锁」或 `tests/lib_semantics/` 钉住（F110 piper 教训） |

原则（M9）：**不需判断力的用例降层**（L2→L3 用脚本脑），需判断力的收敛到 release/weekly
而非 per-commit；「请用户手工验证」视为体系缺陷——先分层吸收（L4→L3→L1→`octo attest`
探针），物理残余才进 attestation 清单（验证吸收原则，2026-07-12 拍板）。

## 2. marker 语义表（与 `octoagent/pyproject.toml` markers 一致）

| marker | 语义 | gate 行为 | 谁跑 |
|--------|------|-----------|------|
| `e2e_smoke` | F087 smoke 5 域集成层（不打 LLM，DI stub） | 保持 deny | pre-commit hook / lane pr |
| `e2e_scripted` | F138 脚本化 LLM 输出驱动的全链确定性 e2e（零真 LLM/零 OAuth）。决策环用 `ScriptedModelClient`（SkillRunner 协议）经 `OctoHarness(model_client=...)` DI；无决策环的管道用对应 LLM 协议的脚本 stub 经公开注入缝进入（F111 compact 先例：message-adapter 协议 stub 经 `BehaviorCompactionService.llm_client`） | 保持 deny | pre-commit hook / lane pr / CI 专属步 |
| `e2e_full` | F087 full 套件——声明真 LLM **意图**（域文件多数确定性，真打见 real_llm 行） | e2e_live conftest 按此 marker 自动开闸 + 240s timeout + rerun 政策 | `octo e2e full` / baseline 全量（凭证在场） |
| `real_llm` | 真发起 LLM/外部网络调用的**事实**子集（F141 D9；现 = `test_e2e_smoke_real_llm.py` + `test_e2e_mcp_skill_pipeline.py` + `test_e2e_behavior_compact_real_llm.py`（F111 合并质量）三文件级 + `test_e2e_delegation_a2a.py::test_domain_8_real_llm_delegate_task` 函数级——同文件域#9/#10 是直调确定性，函数名 real_llm 属化石命名） | 同 e2e_full（叠加标记） | release lane `live-real-llm`（skip 即 FAIL） |
| `e2e_live` | e2e_live 套件正交标记 | — | 与上共存 |

纪律：**新增真打 LLM 的测试必须同时标 `e2e_full + real_llm`**（意图 + 事实）；确定性
e2e_full 域文件不得标 `real_llm`（否则 release deterministic lane 会漏跑它）。已知
pre-existing sloppiness：确定性 e2e_full 文件拿到不必要的 gate=allow（开闸键在 e2e_full
意图 marker）——超 F141 范围，收紧时须同步改 e2e_live conftest 开闸 fixture。

## 3. lane 模式表（`repo-scripts/lane.py`）

```
uv run --project octoagent --no-sync python repo-scripts/lane.py <pr|baseline|release> [--dry-run]
```

exit code：0 = 通过；1 = FAIL；2 = 参数错误；**3 = 彩排**（`--dry-run` 有 planned 未执行
lane——彩排非通过，gate 消费方只认 0）。

| lane | pr | baseline | release | 说明 |
|------|----|----|----|------|
| quarantine-governance | ✅ | ✅ | ✅ | `check-quarantine.py --enforce-review-date`：**过期条目即 FAIL** |
| attestation-signed | — | — | ✅ | `check-attestation.py --require-signed`：非 optional 的 release 项须已签署且 ≤ 90 天（`--attest-max-age` 可调） |
| agent-config-sync | ✅ | — | — | `sync-agent-config.sh --check` |
| frontend-complexity | ✅ | ✅ | ✅ | `check-frontend-complexity.mjs` |
| backend-smoke-scripted | ✅ | — | — | `pytest -m "e2e_smoke or e2e_scripted"`（≈ pre-commit 的 pytest 半边） |
| backend-full | — | ✅ | — | 全 testpaths（含 e2e_live；real_llm 凭证在场即真打，SKIP 记录不阻断） |
| backend-deterministic | — | — | ✅ | 全 testpaths `-m "not real_llm"` |
| frontend-vitest | — | ✅ | ✅ | `npx vitest run` |
| l1-playwright | — | `--with-l1` | — | 本地要装 playwright 浏览器；CI 有独立 job 常跑 |
| live-real-llm | — | — | ✅ live | `pytest -m real_llm`：**exit 0 且 passed ≥ 1 且 unexpected_skip = 0** |
| attest-service | — | — | ✅ live | `octo attest service --json` 解析 `status` 字段；service not_enabled = FAIL |

- **pr 模式的 canonical 执行点是 pre-commit hook**（含 change-policy staged 路由：纯 docs
  fastpath 跳 e2e+前端 / gate 资产 staged 附跑校验 / 生产 src 无伴随测试 WARNING）；
  `lane.py pr` 是同组检查的手动重放。
- **release 强制 live**：`SKIP_E2E` 无效（lane.py 不消费）；`--skip` 不得指向 live /
  attestation-signed（exit 2）；live lane 的 skip 三分类——`GATE_P3_DEVIATION`/「LLM 没触发」
  = deviation（放行）、manual gate/域#5 族 = manual_gate（放行）、**其余（凭证/quota/环境）
  = unexpected → FAIL**（fail-closed：skip reason 措辞漂移只会误伤不会放行；改措辞须同步
  `lane.py ALLOWED_SKIP_PATTERNS`）。
- 报告落 `~/.octoagent/logs/lane/<mode>-<ts>.json`（attest JSON 全文可归档，token 零泄漏）。
- attestation 签署 = 人工执行 action 后在 `attestation-checklist.md` 回填 `last_attested`
  （lane 只核对不代签，Constitution #7）。
- CI（`.github/workflows/feature-007-integration.yml`）= push 触发的 deterministic lane：
  L4+L3 全量（gate=deny，`-n auto --dist=loadgroup`）+ e2e_scripted 步 + 治理步 +
  changed-lines coverage 门；真 LLM 不进 CI。

## 4. 调用纪律（怎么跑测试）

```bash
# 标准调用（主仓）
cd octoagent && uv run --project . --no-sync python -m pytest [选择器]

# worktree 验证（强制 PYTHONPATH 锁，防共享 venv editable 指向漂移 → 假 0 regression）
cd <worktree>/octoagent && env PYTHONNOUSERSITE=1 \
  PYTHONPATH="$(pwd)/packages/core/src:$(pwd)/packages/provider/src:$(pwd)/packages/protocol/src:$(pwd)/packages/tooling/src:$(pwd)/packages/skills/src:$(pwd)/packages/policy/src:$(pwd)/packages/memory/src:$(pwd)/packages/sdk/src:$(pwd)/apps/gateway/src" \
  uv run --project . --no-sync python -m pytest [选择器]
```

- **必须 `python -m pytest`**，禁裸 `uv run pytest`：console-script shebang 可能指向已删
  worktree 解释器 → uv 退化 PATH 全局 python → 脏 sys.path（hook 内嵌 watchdog 注释有完整
  事故记录）。
- **worktree 内禁 `uv sync`**：共享 venv 是 symlink 设计，sync 会把全局 editable 指向改到
  当前树，污染其它并行 worktree 的验证。
- **hook 执行模型**：pre-commit hook 的 `uv run`（无 `--no-sync`）会自动 sync——多 worktree
  并行 commit 后，共享 venv 指向最后 commit 的树；组合态验收/回主仓前先在主仓 `uv sync`
  重指。hook 收集当前树的 conftest 但可能 import 别的树的 src——新增 import 面要防御式
  （先例：`pytest.importorskip("octoagent.skills.testing")`、gate 冗余布线 try/ImportError）。
- 验证按改动回归面裁剪：纯测试新增不必跑全量；改生产代码跑受影响包 + e2e_smoke；
  合 master 前 baseline 全量（路由表见 §7）。

## 5. flaky 三分处置（谁进 quarantine，谁不进）

| 类型 | 处置 | 机制 |
|------|------|------|
| **真 flaky**（时序/环境间歇，可治但未治） | 入册 `octoagent/tests/quarantine.json`（六字段 `id/path/reason/owner/review_after/exit_criteria`） | 根 conftest 给命中 path 前缀的用例加 `flaky(reruns=1)`；`check-quarantine.py --enforce-review-date` 在 **pre-commit 恒跑** + CI + lane 全模式，**过期即门禁 FAIL**——复查后要么治好删条目、要么带新证据续期 |
| **环境永久不适用**（绝对时长性能断言 vs 共享 runner） | 测试内 `skipif(CI)` + 完整理由（F142 两例样板：`test_finalize_result_offload.py` / `test_threat_scanner_boundary.py`） | **不入 quarantine**——无 exit criteria 的永久豁免入册会把「过期即 FAIL」污染成例行盖章（反狼来了）；须有确定性伴测在 CI 照跑 |
| **真 LLM 固有变异性**（e2e_full） | e2e_live conftest 对 `e2e_full` marker 自动 `flaky(reruns=1)` | live 变异性政策（主机制是 GATE_P3_DEVIATION 结构化 SKIP），非 flake 掩盖 |

**禁 blanket rerun**：CI 的 `--reruns 1` 过渡桥已删（F137→F141）；e2e_smoke 已移出
conftest blanket——确定性套件抖动 = 真 bug 或入册，rerun 掩盖是欠账不是修复。

## 6. 红线（Do not）

- **禁 sleep 赌窗口**：`await asyncio.sleep(N)` + assert 是已归档工程债（F083 ~72 处）——
  新用例必须条件轮询（poll until + deadline）或受控时钟；治欠账范式见 F142（f009 条件轮询
  替固定 sleep）。原因：慢 runner 上必抖，然后你会想加 rerun，然后 rerun 掩盖真 race。
- **时序敏感文件必须标 `xdist_group`** 钉同 worker（CI 是 `-n auto --dist=loadgroup`）；
  不标的新时序测试在 CI 并行下会与邻居互踩。
- **新增 stateful 模块单例必须登记** `apps/gateway/tests/e2e_live/helpers/MODULE_SINGLETONS.md`
  并接 hermetic reset——否则跨测试泄漏在 xdist 下不可复现。
- **hermetic：不碰宿主 `~/.octoagent`**（e2e_live 双 autouse fixture 隔离 env/单例；测试
  数据一律 tmp_path）。跑真实例语义的验证用托管实例站用户视角，不在单测里模拟。
- **测试不复制生产算法**（memU 反面教材：以 avoid circular import 为由把算法抄进测试 →
  契约漂移）——import 生产实现或经 DI 注入。
- **第三方库 Protocol Fake 必须配真库签名锁**（F110 piper `synthesize_wav` 教训）：
  `sys.modules` 注入假模块断言调用面，或 `tests/lib_semantics/` 真库钉住。
- **绝对时长阈值断言不进 CI**：按 F142 样板 `skipif(CI)` + 理由 + 确定性伴测。

## 7. 验证路由表（按改动面选验证，别把全量当默认）

| 改动面 | 最小验证 | 何时升级 |
|--------|----------|----------|
| 纯 docs / .specify | 无（hook docs fastpath 自动跳） | — |
| 单包纯逻辑 | `pytest packages/<pkg>/tests/ -k <焦点>` | 跨包接口变更 → 受影响包全量 |
| gateway 服务/路由 | `pytest apps/gateway/tests/ -k <焦点>` + `-m "e2e_smoke or e2e_scripted"` | 触 harness/broker/决策环 → 补跑 `tests/integration` |
| 决策环/工具派发 | `pytest -m e2e_scripted`（8s 级） | 需真判断力验证 → 手动 `octo e2e <域>` |
| 前端 | `npm run check:complexity` + `npx vitest run` | UI 流程语义 → `npx playwright test`（L1） |
| 测试基础设施（conftest/fixture/marker/gate 脚本） | `pytest tests/gate/` + 受影响套件 | 必触发 Codex review（CLAUDE.local「关键测试套件大改」节点） |
| 合 master 前 | `lane.py baseline`（全量） | — |
| 真机部署前 | `lane.py release`（强制 live） | — |

## 8. coverage 门（CI）

- **changed-lines ≥90%**：`check-changed-lines-coverage.py` 用 git diff 新增行 ∩ lcov
  机械计算；范围 = `packages/*/src` + `apps/gateway/src` 的 `.py`；存量不背债；
  范围内新文件无任何覆盖记录 = 按 0 计（新模块必须有测试 import）。
- escape hatch：HEAD commit message 加 `[cov-exempt]`（附原因）——与 `SKIP_E2E` 同级的
  显式可见 bypass，治「忘」不治「恶」（单人仓威胁模型），CI 日志大声记录。
- scope 底线 / 棘轮两重门显式 defer（引入成本高；changed-lines 单条 ROI 最高——M9 审计拍板）。
