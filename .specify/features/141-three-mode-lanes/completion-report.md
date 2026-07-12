# F141 三模式 lane 门禁 — Completion Report

> 2026-07-13。worktree `F141-lanes`（base = origin/master d22378b8）。**未 push**。

## 1. 交付 vs 计划（6 件全交付）

| 件 | 计划（spec） | 实际 | 偏离 |
|----|--------------|------|------|
| 1 三模式 lane 编排 | repo-scripts/lane.py + release 强制 live + attest JSON status 解析 | ✅ 如计划；另增 `--dry-run` 彩排、`--attest-max-age`、JSON 报告落 `~/.octoagent/logs/lane/` | spec 评审后新增 D9：release 切分用新 marker `real_llm` 而非 `e2e_full`（Codex H1）；live lane 判定升级三重（H2 skip 三分类） |
| 2 flaky quarantine | 六字段 manifest + 过期即 FAIL + conftest 定向 rerun | ✅ 如计划；`check-quarantine.py` stdlib 供 hook 直调；conftest 经 importlib 复用校验逻辑（单一事实源） | F142 两个永久豁免**保 skipif 不入册**（任务书「可作首批条目或保 skipif——你判断」→ 判保 skipif，理由见 spec D3，Codex 显式背书）；manifest 初始为空（现无具体已知 flaky 路径） |
| 3 change-policy | 「纯 docs 提速」+「missing-test 警告」两条 | ✅ 如计划；另加 gate 机器资产 staged 附加校验（quarantine.json / attestation-checklist.md） | 无 |
| 4 changed-lines 门 | CI lcov ∩ diff ≥90% + commit message escape hatch | ✅ 如计划；coverage source 进 pyproject 9 src 目录（Codex H3）；e2e_scripted CI 专属步顺带回收 F142 遗留 | `[cov-exempt]` HEAD-only（Codex M1 拒绝带理由：单人仓治「忘」不治「恶」） |
| 5 tests/AGENTS.md | 判定表 + 纪律 + 红线 + marker + lane 表 | ✅ 8 节全；三教义源（CLAUDE.md via .agent-config / testing-strategy / e2e-testing）指针化 | 无 |
| 6 --reruns 桥处置 | quarantine 就位后删 | ✅ CI `--reruns 1 --reruns-delay 2` 已删；e2e_live blanket 收窄 e2e_full-only | e2e_full 保留 marker 级 rerun 定性为「live 变异性政策」非 flake 掩盖（spec D3） |

## 2. 文件面（生产代码零改）

新增：`repo-scripts/{lane,check-quarantine,check-attestation,check-changed-lines-coverage}.py`、
`octoagent/tests/quarantine.json`、`octoagent/tests/AGENTS.md`、`octoagent/tests/gate/`（4 文件
98 用例）、`.specify/features/141-three-mode-lanes/`。
修改：`.githooks/pre-commit`、`.github/workflows/feature-007-integration.yml`（backend job；
frontend / l1-playwright 零触碰）、`octoagent/conftest.py`、`octoagent/pyproject.toml`
（markers + [tool.coverage.run]）、`e2e_live/conftest.py`（blanket 收窄）、
`test_flaky_marker.py`（语义对齐）、`test_e2e_smoke_real_llm.py` + `test_e2e_mcp_skill_pipeline.py`
（+real_llm pytestmark）、docs ×4、`.agent-config/shared.md`（+CLAUDE.md/AGENTS.md 再生）。
`packages/provider/tests`（F139 地盘）与 `frontend/src`（F143 地盘）零触碰。

## 3. 三模式真跑证据

- **pr**：本 Feature 各 commit 即实证——commit#1 时新 hook **当场抓到** `test_flaky_marker.py`
  语义漂移并阻断（改后放行）；docs commit 走 docs-only fastpath（跳 e2e/前端，秒过）；
  `lane.py pr` 手动重放 4 lane 全 PASS（backend-smoke-scripted 6.7s）。
- **baseline**（组合验证，避免真打 LLM）：backend 全量 `-m "not real_llm"`（见 §4）+
  frontend vitest 204 passed + complexity PASS。8 个 real_llm 用例是 release live 半边，
  按任务边界不在本 agent 侧点火。
- **release**：`lane.py release --dry-run` 彩排——quarantine PASS；**attestation-signed 如实
  FAIL**（ATT-129-BOOT `last_attested: null` 从未签署 → 机制咬人即设计）；其余 planned；
  exit 1。`SKIP_E2E=1` 下告警「release 模式无效」并照常执行。live 半边执行指引见 §6。
- **changed-lines 门首跑预演**：对 F141 自身 diff（base=origin/master）真跑 gate 脚本 →
  `PASS 本次 diff 无范围内生产源码新增行`（F141 零生产代码，zero-scope 路径）——CI 首跑
  预期一致。真 lcov SF 路径形态已实测验证（`packages/policy/src/...` 相对路径 = 假设）。

## 4. 回归

- 全量确定性（`-m "not real_llm"`，PYTHONPATH 锁本 worktree + `--no-sync`）：
  **5157 passed / 0 failed / 14 skipped / 8 deselected（=real_llm）/ 1 xfailed / 1 xpassed，
  191s**。xfail/xpass 均为 F124 存量 strict=False 站点（test_threat_scanner.py 中文
  pattern），非 F141 触碰。vs master 组合态基线 5060 passed：+98 gate 测试 + 其余
  对账为 real_llm 移出/既有 skip 波动，0 failed 即 0 regression。
- pr lane 集（`-m "e2e_smoke or e2e_scripted"`）：24 passed + 1 skipped（piper importorskip
  模块级门，F142 设计）~4.6s。
- gate 新测试：98 passed。
- CI e2e_scripted 步预演（scrubbed HOME 模拟无凭证 CI）：16 passed / 26s（含 coverage）。
- frontend vitest：204 passed。

## 5. Codex spec 评审闭环（3H + 2M + 1L）

| # | 结论 | 处置 |
|---|------|------|
| H1 e2e_full 非真打单一信号 | 接受 | D9 新增 `real_llm` marker（事实标记）；release 切分不复用 e2e_full |
| H2 passed≥1 挡不住 1 pass + 大量 skip | 接受 | D4v2 skip 三分类（deviation / manual_gate 放行；unexpected → FAIL；fail-closed） |
| H3 relative_files 需配 source | 接受 | [tool.coverage.run] source 9 目录 + CI 裸 --cov；真 lcov 实测验证 |
| M1 [cov-exempt] HEAD-only 空提交可整推豁免 | **拒绝带理由** | 单人仓威胁模型治「忘」不治「恶」；per-commit trailer 无额外防护只加摩擦；缓解=大声记录 sha+subject |
| M2 lane --no-sync 与 hook sync 漂移窗口 | 接受 | lane.py pytest/attest 子进程 PYTHONPATH 锁本树（build_pytest_env，单测钉 8+1 src） |
| L1 docs fastpath 跳过 tests/AGENTS.md | **拒绝带理由** | AGENTS.md 非机器解析资产；机器消费的 gate 资产（json/yaml）有 staged-trigger 校验 |

## 6. release live 半边——主 session 执行指引（本 agent 不真打 LLM）

在部署机（本机）主仓（合入后）执行：

```bash
uv run --project octoagent --no-sync python repo-scripts/lane.py release
```

预期与决策点：
1. `attestation-signed` 会 FAIL 直到 ATT-129-BOOT 首次签署（重启 Mac → `octo service status`
   运行中 + /ready 绿 → 在 attestation-checklist.md 回填 `last_attested: "YYYY-MM-DD"`）。
2. `live-real-llm` 真打 GPT-5.5（8 用例，~5-10min，消耗 ChatGPT Pro 配额）；判定 = exit 0 且
   passed≥1 且 unexpected_skip=0（deviation/manual-gate skip 记录不阻断）。
3. `attest-service` 会 SIGKILL 真实例（秒级闪断后自愈）；`attest-remote` 当前本机未部署
   Tailscale → not_enabled → 默认 FAIL——确认暂不部署远程则 `--allow-not-enabled` 显式降 WARN。
4. 报告归档 `~/.octoagent/logs/lane/release-<ts>.json`。

## 7. 已知 limitations / 后续

- **CI 首跑预期**：①changed-lines 门对 F141 push 走 zero-scope PASS（无生产新增行）；
  ②删 reruns 桥后若 sleep 断言在 2-core 抖红 → 逐条入册 quarantine.json（机制生效方式）；
  ③`--cov` 使 backend job 变慢（预估 +20~50%，5m11s → ~7min），首跑记录实际值；
  ④workflows push 须 SSH（OAuth 无 workflow scope，运维备忘）。
- coverage lcov 对从未 import 的 source 文件可能无 SF 记录（实测 policy 子集只出 9 SF）——
  gate 对 scoped 新文件缺记录**按 0 覆盖计**，方向 fail-closed，无假绿面。
- e2e_full 意图 marker 给确定性域文件不必要的 gate=allow：pre-existing sloppiness，
  收紧须同步 e2e_live conftest 开闸 fixture（AGENTS.md §2 已记）。
- live lane skip 分类靠 reason 文本匹配（`ALLOWED_SKIP_PATTERNS`）：措辞漂移 → 划成
  unexpected → FAIL（误伤方向）；改 skip 文案须同步 lane.py 清单（AGENTS.md §3 已记）。
- scope 底线 / 棘轮 coverage 门、weekly canary 编排：显式 defer（spec §4）。
- 主仓 venv：本 worktree hook 的 uv run 已把共享 venv 指向本树——合入/验收后主仓跑一次
  `uv sync` 重指（既有运维备忘）。

## 8. living-docs 漂移闸

- testing-strategy.md §13.1：lane 📋→✅（策略骨架 + 指针，不复制表格）✅
- e2e-testing.md §3.1 hook 描述 + F138 keystone「已归入 pr lane」✅
- milestones.md F141 行 ✅ 六件摘要 ✅
- CLAUDE.md/AGENTS.md（.agent-config 再生流程）测试行指针 ✅
- pyproject markers 文案与实况对齐（e2e_full 意图/事实二分、e2e_scripted 已兑现）✅
