# F138 — 实施计划（Phase 拆分）

> 依赖 spec.md 3 设计岔路拍板后启动。分支 `feature/138-scripted-llm-harness`（off master `8fb1386e`）。
> worktree 验证禁 uv sync，PYTHONPATH 锁 + `uv run --project octoagent --no-sync python -m pytest`。**不主动 push 等拍板**。

---

## 依赖拍板（阻塞实施）

| 岔路 | 阻塞的 Phase | 推荐（spec §2）|
|------|-------------|---------------|
| ① 脚本件放哪 + 私有 API 复用 | Phase B, D | skills 包 `testing` 子模块 + 私有 `_JsonSchemaTestData` 复用 + 签名锁 |
| ② 脚本脑 vs schema-fill 优先级 | Phase D 是否首版 | 可编程脚本脑优先（QueueModelClient），SchemaTestAdapter 作 Phase 2 |
| ③ 替换 vs 并存 + echo 解耦 | Phase A | 并存 + model_client override 与 llm_mode 解耦 |

---

## Phase 顺序（先简后难，先建 baseline 信心——沿用 F091/F094 范式）

### Phase A — harness DI 注入点（keystone 地基，最高风险先做）
- `OctoHarness.__init__` 加 `model_client` + `clock` 两参（`octo_harness.py:123-143`）。
- `_bootstrap_executors` 拦截硬连（`:1134-1157`）：override 非 None → 无条件建 SkillRunner（岔路③子决策，与 llm_mode 解耦）；None 分支逐行保留原 `ProviderModelClient(...)`。
- `app.state.clock` seam（bootstrap 一处，默认 `lambda: datetime.now(UTC)`）。
- **AC-1, AC-2**。**gate：byte-for-byte None 等价对账**（bootstrap 产物 diff master vs 改后，全 None 时零差异）——F113 字节级对账范式。
- 产物：`test_octo_harness_model_client_di.py` + `test_octo_harness_di_none_equivalence.py`。

### Phase B — QueueModelClient 上提（脚本脑主力）
- 新建 `packages/skills/src/octoagent/skills/testing/scripted_model.py`：`ScriptedModelClient`（QueueModelClient 改名+上提，实现零逻辑变更）。
- `packages/skills/tests/conftest.py:80-104` 改 re-export（`QueueModelClient = ScriptedModelClient` 或 import 别名），保 `test_runner.py` 零改。
- **AC-4, AC-5**。产物：`test_scripted_model_multistep.py`；既有 `test_runner.py` 回归。

### Phase C — keystone L3 e2e（验收锚）
- fake credential_store fixture（无宿主 OAuth 依赖）+ scripted_harness fixture（`model_client=ScriptedModelClient([...])`）。
- `test_e2e_scripted_decision_loop.py`：driven via `llm_service.call(..., metadata={selected_tools})` → 断言决策→派发→回写全链 + 零真 provider HTTP。
- 新 marker `e2e_scripted`（pyproject.toml markers 登记）。
- **AC-3【keystone】, AC-8（CI-runnable）**。
- **这是本 Feature 的验收锚——Phase C 绿即证 keystone 打通。**

### Phase D — SchemaTestAdapter（Phase 2 便利层，岔路②拍板决定是否首版）
- 同模块加 `SchemaTestAdapter`：`generate` 用 `tool_broker.discover()`（provider_model_client.py:272 同路径）+ `_JsonSchemaTestData(tool_meta.parameters_json_schema, seed).generate()` 组 tool_calls。
- `test_schema_test_data_api_lock.py`：sys.modules 注入 fake pydantic_ai 断言调的是 `_JsonSchemaTestData(schema, seed).generate()` 签名（F110 库签名锁范式，防私有 API 升级静默漂移）。
- **AC-7**。**若岔路②拍"脚本脑优先"→ Phase D 可推 F138 Phase 2 或独立跟进**。

### Phase E — clock DI consumer（次要，坐实 bug 价值）
- watchdog `detectors/cooldown/scanner.py` 的 `datetime.now(UTC)`（≤5 处）改读注入 clock。
- **AC-6**：固定时钟确定性测 watchdog 时间判断（F103d offset-naive 类 bug 在 L4 可抓）。
- 其余 68 处 `datetime.now` 不动（F142）。

### Phase F — 文档漂移修 + verify（living-docs 闸）
- `e2e-testing.md:14-15` DI 清单诚实化（删 secret_store/transport_factory/clock 虚构，加真实 model_client/clock）。
- `testing-strategy.md §13` TestModel/FunctionModel 从"愿景"改"已落地"（指向 `skills/testing/scripted_model.py`）+ 删 LiteLLM 残留引用。
- completion-report.md：实际做 vs 计划 + 岔路拍板结果 + 已知 limitations。
- **AC-9**：全量回归 0 regression + e2e_smoke 8/8 + 新 e2e_scripted 绿。
- Codex + Opus 双评审 0 HIGH。

---

## 关键不变量（每 Phase 守）

1. **生产零影响**：`model_client=None AND clock=None` 逐行等价 master（Phase A gate）。
2. **22 Echo L3 测试零回归**（岔路③并存，Echo 路径 A 不碰）。
3. **`test_runner.py` 零改动**（Phase B re-export 兼容）。
4. **脚本化不可达生产**（#9：`testing` 命名空间 + DI 默认 None + main.py 不传）。
5. **私有 API 有锁**（Phase D 若做，`_JsonSchemaTestData` 配签名锁测试）。

---

## 验证命令（worktree PYTHONPATH 锁）

```bash
# 单 Phase focused（设计阶段基本不跑，实施阶段用）
cd octoagent && PYTHONPATH=... uv run --project . --no-sync python -m pytest <focused> -x
# keystone
... python -m pytest apps/gateway/tests/e2e_live/test_e2e_scripted_decision_loop.py -v
# 全量回归 gate（Phase F）
... python -m pytest -q     # 对账 ≥ master baseline passed，0 regression
```

---

## 风险 / 已知坑

| 风险 | 缓解 |
|------|------|
| harness bootstrap 是核心，改错 = 全栈崩 | Phase A 先行 + byte-for-byte None 等价对账 + focused 测试先绿 |
| model_client override 若被 echo-skip 门挡住 → SkillRunner 不建 → keystone 测不了 | 岔路③子决策：override 非 None 时**无条件**建 SkillRunner（与 llm_mode 解耦），显式测（AC-1）|
| `_JsonSchemaTestData` 私有符号升级漂移 | 签名锁测试（Phase D）；或岔路①子决策改自写 schema-walker |
| 上提 QueueModelClient 破 test_runner.py | re-export 别名，既有测试零改（AC-5 硬验）|
| clock DI 引诱范围蔓延到 73 处 | 严守 F138 只 seam + ≤5 watchdog，其余归 F142（spec §1.3）|
| 新 e2e_scripted marker 未登记 → 测试被 deselect | pyproject.toml markers 登记 + verify 阶段 grep 确认 |
