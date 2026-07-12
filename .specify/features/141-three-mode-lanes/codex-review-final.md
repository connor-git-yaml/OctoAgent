# F141 final 评审记录（Codex gpt-5.4 high 对抗式 + Opus 自审，2026-07-13）

## Codex final：2 HIGH + 1 MED + 1 LOW

### H1 `real_llm` 漏标 delegation 域#8 ✅ 接受已修

- Codex 抓实：`test_e2e_delegation_a2a.py::test_domain_8_real_llm_delegate_task` 层2 经
  `/api/message` 真打 LLM（GATE_P3_DEVIATION skip 兜底）——审计「只有 2 个真打文件」
  与我的 grep 复核都漏了它（文件 docstring 只在域#10 处写「直调」，域#8 的真打面藏在
  层2 代码里）。**旁证**：终门全量跑（当时未标记）把它包含在 deterministic 选择里——
  正是 H1 描述的洞真实发生（该跑可能真打了 ≤1 次 LLM）。
- 修复：域#8 函数级 `@pytest.mark.real_llm`（同文件域#9/#10 是直调确定性，函数名
  `real_llm` 属化石命名，不标）；`-m real_llm` 8→9 用例；delegation 文件
  `-m "not real_llm"` 实测 2 passed 1 deselected。AGENTS.md / spec / testing-strategy /
  milestones / completion-report 的「2 文件」表述全部修正。

### H2 `release --dry-run` 可 exit 0 冒充通过 ✅ 接受已修

- 修复：exit code 语义 fail=1 > **planned=3** > pass=0——`--dry-run` 有未执行 lane 时
  恒非 0（彩排非通过，gate 消费方只认 0）；stderr 明示「彩排完成（exit 3，非通过）」。
  新增单测 `test_dry_run_exit_3_not_zero`；lane.py docstring / --help / AGENTS.md 同步。

### M1 quarantine 过期只在 CI/lane 拦，canonical pr 入口（hook）漏 ✅ 接受已修

- 修复：pre-commit 恒跑 `check-quarantine.py --enforce-review-date`（stdlib python3，
  空 manifest <100ms；置于 docs fastpath **之前**——任何 commit 都拦过期）；原
  change-policy 内 staged 触发的 schema-only 分支删除（恒跑版是超集）。
  本地 plain pytest / IDE 仍不拦过期（conftest 只做 schema 校验保持 cc-haha 分工——
  在 conftest 拦会让「条目过期当天」全球 pytest 入口同时爆炸，迭代体验换护栏不值；
  commit/CI/lane 三入口已闭合）。

### L1 missing-test 警告 case glob「只匹配 src 下一层」 ❌ 拒绝（实证推翻）

- bash `case` 的 `*` 是 fnmatch 语义**跨 `/`**（非 pathname 模式）。实测：
  `octoagent/packages/core/src/octoagent/core/store/y.py` /
  `octoagent/apps/gateway/src/octoagent/gateway/services/deep/x.py` /
  `octoagent/frontend/src/pages/A.tsx` 三类深路径全命中 PROD 分支。Codex 按
  pathname-glob 直觉误判；无需改动。

## Codex 四个必答挑战结论（修复后）

1. release 堵 SKIP_E2E：**通过**（SKIP_E2E 不被消费仅告警；--skip 拒 live；
   PYTEST_ADDOPTS 无可行假绿面——Codex 原话）+ H2 修复后 --dry-run 也不再能冒充。
2. quarantine 过期真 FAIL：M1 修复后 commit（恒跑）/ CI / lane 三入口全闭合。
3. changed-lines 真机械：**通过**（diff/lcov/zero-scope/新文件 0 计/[cov-exempt]/
   fetch-depth/diffbase/--cov-append 全查无错）。
4. AGENTS.md 零漂移：H1 修复 + 表述同步后闭合；hook 覆盖面质疑被 L1 实证推翻。

## Codex 附加审视（无需改动项）

- hook 边界：空 staged / 带空格文件名 / `docs-foo/` 误判——全查无洞；rename 被
  `--name-only` 丢语义有余风险但未升级 finding（记录在案）。
- `_run_attest` 的 `python -c ... main()` Click 传参：可工作。
- conftest 跨树加载 quarantine：防御充分（`__file__` 定位，不依赖 master 树有脚本）。

## Opus（主 agent）自审补充

- 终门全量在 H1 修复前跑过 → 数字不受 marker 影响（当时 domain#8 被包含且通过；
  修复后它只是移到 live lane，不改变其余 5156 个结果）；delegation 文件定向复验
  2 passed 1 deselected。
- lane.py exit 3 变更后 gate 套件 102 passed 重验。
- 检查了 fastpath 对 `.agent-config/**` 的非 .md 文件（如 templates 改名）不会误入
  docs-only（`case` 三分支只认 docs/ .specify/ *.md）。
- `--cov-append` 首步 `--cov-report=` 空报告 + 次步产 lcov 的顺序已在本地全链模拟
  （policy 子集真 lcov SF 形态 = 假设）。
- 残留已知面（非 HIGH）：live lane skip 分类靠文本匹配（fail-closed）；lcov 对未
  import 文件可能无记录（gate 按 0 计，fail-closed）；均记录于 AGENTS.md / report。

**收敛：2 HIGH + 1 MED 全修复，1 LOW 实证拒绝——0 HIGH 残留。**

## Codex re-review（fix commits d07a0869 + aba41d93，F099 教训「修复可能引新问题」）

四点定向核验全通过、0 新 finding：①exit 语义 skipped_explicit 不误算 planned、
非 dry-run 全跑仍 0/1、报告 exit_code 与返回值同源；②hook 恒跑段短路正确、旧分支缺
脚本不误 FAIL、变量不冲突；③real_llm 函数级仅排域#8，#9/#10 不受累；④HOME
monkeypatch 对 Path.home() 有效（Codex 实测 CPython 路径解析）。**双评审最终收敛。**
