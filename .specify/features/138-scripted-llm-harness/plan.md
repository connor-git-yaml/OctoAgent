# F138 — 实施计划（Phase 拆分，v2 收窄版）

> 三设计岔路已拍板（spec §2 已写死），本 plan 为实施版。分支 `feature/138-scripted-llm-harness`（rebase onto master `a1e4ca15`）。
> worktree 验证禁 uv sync，PYTHONPATH 锁 + `uv run --project . --no-sync python -m pytest`。**不主动 push 等拍板**。
> Baseline（rebase 后未改动 worktree 全量实测）：**4919 passed / 14 skipped / 1 xfailed / 1 xpassed（379s）**。

---

## 拍板结果（阻塞已解除）

| 岔路 | 拍板 | 影响 |
|------|------|------|
| ① 脚本件放哪 | skills 包 `testing` 子模块（随包发布）| Phase B 落 `packages/skills/src/octoagent/skills/testing/` |
| ② 脚本脑 vs schema-fill | **脚本脑优先（用户拍板）**，SchemaTestAdapter → Phase 2 deferred | **Phase D 本次跳过**，spec §2.2 归档 deferred 理由与范围 |
| ③ 替换 vs 并存 | 并存 + override 与 llm_mode 解耦 | Phase A 的 override 分支无条件建 SkillRunner |

---

## Phase 顺序（先地基后验收锚——A 最高风险先做）

### Phase A — harness DI 注入点（keystone 地基）
- `OctoHarness.__init__` 加 `model_client` + `clock` 两参（`octo_harness.py:123-143`，默认 None）。
- `_bootstrap_executors` 拦截硬连（`:1134-1157`）：override 非 None → 无条件建 SkillRunner（拍板③子决策，与 llm_mode 解耦，**不要求 provider 凭证**）；None 分支**原 `if` 改 `elif`、块体逐行不动**（最小 diff 形状，spec §3.2）。
- `bootstrap()` 入口 `app.state.clock` seam（默认 `lambda: datetime.now(UTC)`，additive inert）。
- **AC-1, AC-2**。gate：None 行为等价对账（spec §3.2 精确语义）+ focused 回归。
- 产物：`apps/gateway/tests/test_octo_harness_model_client_di.py` + `test_octo_harness_di_none_equivalence.py`。

### Phase B — QueueModelClient 上提（脚本脑主力）
- 新建 `packages/skills/src/octoagent/skills/testing/{__init__.py,scripted_model.py}`：`ScriptedModelClient`（QueueModelClient 改名+上提，实现零逻辑变更；只依赖 skills 自身模型 + stdlib）。
- **conftest 本 Phase 不动**（spec §3.5 flip-at-the-end：翻转放 Phase F 尾部独立 commit，防 pre-commit hook master-src 收集炸）。
- **AC-4**。产物：`packages/skills/tests/test_scripted_model_multistep.py`（顶部 `pytest.importorskip("octoagent.skills.testing")`）。

### Phase C — keystone L3 e2e（验收锚）
- fake credential_store fixture（`CredentialStore(空 tmp 路径)`，load 返回空 store，无宿主 OAuth 依赖）+ scripted_harness fixture（`model_client=ScriptedModelClient([...])`）。
- `apps/gateway/tests/e2e_live/test_e2e_scripted_decision_loop.py`：driven via `llm_service.call(..., metadata={"selected_tools_json": [...], "permission_preset": "full"})` → 断言决策→broker 派发→回写全链 + provider_router bomb 证零真调用（spec §4）。
- 新 marker `e2e_scripted`（pyproject.toml markers 登记一行——**F137 合并交点，报告显式列出**）+ 文件顶部 `pytest.importorskip("octoagent.skills.testing")` + **不标 e2e_smoke**。
- **AC-3【keystone】, AC-8（CI-runnable）**。Phase C 绿即证 keystone 打通。

### Phase D — SchemaTestAdapter（**本次跳过，Phase 2 deferred**）
- 拍板②：不实施。deferred 范围/理由/启动条件归档在 spec §2.2；completion-report 列入 deferred 清单。

### Phase E — clock DI consumer（坐实 bug 价值）
- watchdog `detectors.py:87,167,227` + `cooldown.py:44,79` + `scanner.py:201` 共 6 处 `datetime.now(UTC)` 改读构造注入 clock（构造参数默认 None → 行为等价）；harness `_bootstrap_optional_routines` 传入与 `app.state.clock` 同一 callable。
- **AC-6**：固定时钟确定性测 watchdog 时间判断（F103d offset-naive 类 bug 在 L4 可抓）。
- 其余 ~67 处 `datetime.now` 不动（F142）。产物：`apps/gateway/tests/test_watchdog_clock_di.py`。

### Phase F — 文档漂移修 + conftest 翻转 + verify（living-docs 闸）
- `docs/codebase-architecture/e2e-testing.md` DI 清单诚实化（删从未存在的 `secret_store/transport_factory/clock` 旧文案，写真实 6 DI：credential_store/llm_adapter/mcp_servers_dir/data_dir/plugins_dir/model_client + clock）。
- `docs/blueprint/testing-strategy.md` §"Agent 决策环测试" TestModel/FunctionModel 从"愿景"改"已落地"（指向 `skills/testing/scripted_model.py`）+ 清 LiteLLM 残留引用。
- `docs/blueprint/milestones.md` M9 表 F138 标 ✅。
- `docs/codebase-architecture/harness-and-context.md` DI 段同步（若有相应段）。
- **conftest 翻转 commit（尾部独立）**：`packages/skills/tests/conftest.py:80-104` 类删除 → `from octoagent.skills.testing import ScriptedModelClient as QueueModelClient`；该 commit 起 `SKIP_E2E=1` + 以 PYTHONPATH 锁定 `pytest -m e2e_smoke` 8/8 作补偿 gate（spec §3.5）。
- completion-report.md：实际做 vs 计划 + 拍板执行情况 + deferred 清单 + F137 合并交点 + 已知 limitations。
- **AC-5, AC-9**：全量回归 0 regression（vs baseline 4919 passed）+ e2e_smoke 8/8（锁定跑法）+ e2e_scripted 绿。
- Codex + Opus 式双评审 0 HIGH。

---

## 关键不变量（每 Phase 守）

1. **生产零影响**：`model_client=None AND clock=None` 行为等价 master（spec §3.2 精确语义；Phase A gate）。
2. **22 Echo L3 测试零回归**（拍板③并存，Echo 路径 A 不碰）。
3. **`test_runner.py` 等 8 个既有消费文件零改动**（Phase F re-export 兼容，AC-5 硬验）。
4. **脚本化不可达生产**（#9：`testing` 命名空间 + DI 默认 None + main.py 不传 + 无生产开关）。
5. **pre-merge 窗口每 commit hook 可过或显式 SKIP_E2E+补偿**（spec §3.5 防御三件套）。
6. **不动 F137 地盘**：`provider_client.py` / `fallback.py` / `llm_service.py` / `.githooks` / `.github` 零触碰；pyproject 仅 markers 一行（显式合并交点）。

---

## 验证命令（worktree PYTHONPATH 锁）

```bash
WT=/Users/connorlu/Desktop/.workspace2.nosync/OctoAgent/.claude/worktrees/F138-scripted-llm
PP="$WT/octoagent/packages/core/src:$WT/octoagent/packages/provider/src:$WT/octoagent/packages/memory/src:$WT/octoagent/packages/skills/src:$WT/octoagent/packages/tooling/src:$WT/octoagent/packages/policy/src:$WT/octoagent/apps/gateway/src"
cd "$WT/octoagent"
# focused（每 Phase）
PYTHONPATH="$PP" PYTHONNOUSERSITE=1 uv run --project . --no-sync python -m pytest -q -p no:cacheprovider <focused> -x
# keystone
... python -m pytest apps/gateway/tests/e2e_live/test_e2e_scripted_decision_loop.py -v
# 全量回归 gate（Phase F）：对账 baseline 4919 passed
... python -m pytest -q -p no:cacheprovider
# e2e_smoke 补偿 gate（conftest 翻转后）
... python -m pytest -q -p no:cacheprovider -m e2e_smoke
```

---

## 风险 / 已知坑

| 风险 | 缓解 |
|------|------|
| harness bootstrap 是核心，改错 = 全栈崩 | Phase A 先行 + None 行为等价对账（elif 最小 diff 形状）+ focused 测试先绿 |
| model_client override 被 echo-skip 门挡住 → SkillRunner 不建 | 拍板③子决策：override 非 None 时**无条件**建 SkillRunner，显式测（AC-1）|
| pre-commit hook master-src 收集炸（新模块 import）| spec §3.5 防御三件套：importorskip + flip-at-the-end + 不标 e2e_smoke |
| 上提 QueueModelClient 破 test_runner.py | Phase F 尾部 re-export 别名，既有测试零改（AC-5 硬验）|
| clock DI 引诱范围蔓延到 73 处 | 严守 seam + watchdog 6 处，其余归 F142（spec §1.3）|
| 新 e2e_scripted marker 未登记 → strict-markers/警告 | pyproject.toml markers 登记 + verify 阶段 grep 确认 |
| pyproject markers 与 F137 同文件改动 | 仅一行增量；报告显式列为合并交点，主 session rebase 按序解 |
