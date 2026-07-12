# F141 spec 评审记录（Codex gpt-5.4 high，对抗式，2026-07-13）

> `codex exec` 对照仓库实况审 spec/plan。3 HIGH + 2 MED + 1 LOW。处理如下。

## HIGH（3/3 接受）

### H1 `e2e_full` 不是「真 LLM 单一信号」——lane 切分不能复用它 ✅ 接受

- 证据：`test_e2e_notification_persist.py` / `test_e2e_file_workbench.py` / `test_e2e_ssrf_guard.py` /
  `test_e2e_tool_result_threat_scan.py` 等确定性直调测试也打了 `e2e_full` marker（审计早有记录：
  「9 个域文件 7 个不打 LLM」）。若 release deterministic lane 用 `-m "not e2e_full"`，这批确定性
  测试被错误赶出 deterministic lane；live lane `-m e2e_full` 又会被它们的确定性 PASS 满足
  `passed>=1` → 假绿。
- **修法（spec 修订 D9）**：新增 marker `real_llm`（真发起 LLM/外部网络调用的用例，`e2e_full` 子集），
  pytestmark 加到实际真打的 2 个文件（`test_e2e_smoke_real_llm.py` + `test_e2e_mcp_skill_pipeline.py`，
  审计 L2 finding 钉过「真打 LLM 只有 2 个文件」，实施期再 grep 复核）。release
  deterministic lane = `-m "not real_llm"`；live lane = `-m real_llm`。`e2e_full` 语义（gate 开闸信号 +
  `octo e2e full` 套件）不动——确定性 e2e_full 测试拿到不必要的 gate=allow 属 pre-existing
  sloppiness，超本 Feature 范围，AGENTS.md 记录。

### H2 `passed>=1` 挡不住「1 pass + 大量 SKIP」 ✅ 接受

- **修法（spec 修订 D4v2）**：live lane 判定升级为 skip 分类制——解析 junit skipped reason：
  - `deviation_skip`（reason 含 GATE_P3_DEVIATION）：设计内 LLM 未命中，记录不阻断；
  - `manual_gate_skip`（reason 命中人工闸 env 清单，如 OCTOAGENT_E2E_PERPLEXITY_API_KEY）：记录不阻断；
  - **`unexpected_skip`（其余一切：凭证缺失/quota/环境）：≥1 即 lane FAIL** 并列出 nodeid+reason；
  - 仍保留 exit 0 且 passed ≥ 1 底线。
  报告输出四类计数。allowed-pattern 清单实施期按真实 skip reason 文本钉住并单测。

### H3 `relative_files` 需配 config `source` 才稳 ✅ 接受

- **修法（spec 修订 D5v2）**：`[tool.coverage.run]` 写 `relative_files = true` + `source` 显式列
  9 个 src 目录（`packages/*/src` × 8 + `apps/gateway/src`——目录形态避开 namespace 包按模块名
  解析的不确定性）；CI 用裸 `--cov`（无值 → 走 config source）。SF 路径 = 相对 `octoagent/` 的
  `packages/.../src/...`，脚本补 `octoagent/` 前缀与 git 路径对齐，单测钉住。

## MED（1 接受 / 1 拒绝带理由）

### M1 `[cov-exempt]` HEAD-only 可用空提交整推豁免 ❌ 拒绝（带理由）

- 理由：单人仓威胁模型是「未来自己忘」不是「对抗性开发者」；逃生门本来就要有整推 bypass 能力
  （`SKIP_E2E=1` 先例同样整跳）；per-commit trailer 在同一人写全部 commit 的仓里不提供额外防护，
  只加流程摩擦。缓解：gate 以 exempt 状态 PASS 时**大声记录**（HEAD sha + subject 进 CI 日志与
  step summary），AGENTS.md 写明该语义层级（=SKIP_E2E 同级显式 bypass）。

### M2 lane.py `--no-sync` 与 hook sync 的 venv 漂移窗口 ✅ 接受

- **修法（spec 修订 D1v2）**：lane.py 调 pytest 时显式 **PYTHONPATH 锁自身 repo 树**（从
  `__file__` 推 repo root，拼 9 个 src 目录 + `PYTHONNOUSERSITE=1`）——PYTHONPATH 先于
  site-packages，venv editable 指向哪个 worktree 都不影响被测代码来源（memory 纪律的机械化）。
  stale venv 缺新依赖时 import error 快败，报错带「主仓 uv sync」提示。

## LOW（1 拒绝带理由）

### L1 docs fastpath 会跳过 `octoagent/tests/AGENTS.md` 改动 ❌ 拒绝（带理由）

- 理由：AGENTS.md 是**人/agent 可读**的判定表文档，lane.py / hook / CI **不解析它**——机器消费的
  gate 资产只有 `quarantine.json` 与 `attestation-checklist.md`，两者已有 staged-trigger 校验
  （fastpath 下同样触发）。对 AGENTS.md 改动跑 e2e_smoke 并不能校验其内容准确性（那是 review
  职责），排除它出 fastpath 只是安慰剂。AGENTS.md 开头注明「本文不被机器解析；机器消费资产
  另列」以固化该边界。

## 评审确认（无需改动）

- D3 永久 skipif 不入册：Codex 显式背书（「塞进 quarantine 反而污染过期即 FAIL 语义」）——
  任务书「现有已知项入册」的偏离成立。
- conftest 读 quarantine.json 走 `__file__` 相对定位：跨 worktree 场景无实证风险（数据文件非
  import，与 venv 漂移正交）——按 plan 落实即可。
- `--cov-append` 语义、`.agent-config/templates` fastpath 有 sync-check 兜底：验证通过。
