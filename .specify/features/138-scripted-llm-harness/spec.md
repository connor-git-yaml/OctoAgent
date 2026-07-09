# F138 — 脚本化 LLM harness（L3 决策环确定性覆盖）

> M9 P0 **keystone** · 规模 L · 分支 `feature/138-scripted-llm-harness`（off master `8fb1386e`）
> **设计先行**：本 spec + plan + research 回用户拍板 3 设计岔路后再实施。**不实施、不 push**。
> 定位：M9 撬动最大的一块——打通 agent 决策环的 L3 确定性覆盖，把大半"不需判断力"的 L2 用例降层到 L3 成本。

---

## 0. 现状诊断（复核主 session 判断，行号以 master `8fb1386e` 实读核实）

| # | 判断 | 复核结论 | 证据 file:line |
|---|------|----------|----------------|
| ① | L3 测不了 agent 决策环 | **成立**。决策环唯一入口 `SkillRunner.run → model_client.generate`，`model_client` 硬连 `ProviderModelClient`，harness 无注入点 | `octo_harness.py:1136-1141`；`__init__:123-143`（5 DI 无 model_client）|
| ② | `llm_adapter` 只替 primary 不进 SkillRunner | **成立**。`llm_adapter` → `FallbackManager.primary`（路径 A 纯文本），SkillRunner 的 `model_client` 是另一对象（路径 B）| `octo_harness.py:747-757` vs `:1136-1141`；`llm_service.py:314,327` |
| ③ | echo 模式跳过 SkillRunner | **成立**。`if _llm_mode_env != "echo":` 才建 SkillRunner，echo 直接 `skill_runner_skipped` | `octo_harness.py:1134,1155-1157` |
| ④ | `QueueModelClient` 已能产 tool_calls 驱动决策环 | **成立**。~24 行无状态队列件，实现完整 `StructuredModelClientProtocol.generate`，`test_runner.py` 全套消费 | `packages/skills/tests/conftest.py:80-104` |
| ⑤ | L3 现绕过决策环 = 直调 `tool_broker.execute` | **成立**。smoke case 直接 `tool_broker.execute("user_profile.update", ...)`，`generate`/循环全不跑 | `test_e2e_basic_tool_context.py:1-24`（docstring 明写"不真打 LLM"）|
| ⑥ | 协议可脚本化 | **成立**。`StructuredModelClientProtocol` 单方法 `generate`，`clear_history`/`token_usage` 均可选（getattr/hasattr） | `protocols.py:19-32`；`runner.py:755,167,174` |
| ⑦ | pydantic-ai 已是依赖，`_JsonSchemaTestData` 可导入 | **成立**。`pydantic_ai==1.63.0`，`reflect_tool_schema` 本就用 `pydantic_ai._function_schema`；私有符号 `_JsonSchemaTestData` 实测可 import | `schema.py:16,105`；venv 实测 |

**核心缺口一句话**：决策环的"决定调哪个工具"（前半段）在 L3 零覆盖；补 harness `model_client` DI + 上提脚本化 client 即打通。

**顺带纠正的文档漂移**（living-docs 闸，F138 顺手修）：`docs/codebase-architecture/e2e-testing.md:14-15` 宣称 harness DI 是 `credential_store/secret_store/transport_factory/clock`——后三个**从未存在于代码**；`docs/blueprint/testing-strategy.md §13` 的 TestModel/FunctionModel/VCR/ALLOW_MODEL_REQUESTS 全未落地（引用已退役 LiteLLM）。

---

## 1. 目标与范围

### 1.1 目标（keystone 验收锚）

让 63 工具的 OctoHarness 全栈链路在 **L3（不打真 LLM）** 也能跑一遍 **agent 决策环**：`决策 → 工具派发 → 回写`。即：一个**脚本化 `model_client`** 驱动**真** `SkillRunner` → **真** `tool_broker.execute` → **真**回写（USER.md / 事件 / artifact），全程零真 provider HTTP。

### 1.2 In scope

1. **harness `model_client` DI 注入点**（`OctoHarness.__init__` 新增 `model_client` 参数 + 拦截 `octo_harness.py:1136-1141` 的硬连）。
2. **`QueueModelClient` 上提**为可发布的 `testing` 子模块共享件（岔路②的可编程脚本脑，已存在）。
3. **`SchemaTestAdapter`**（TestModel 等价：按工具 schema 自动填合法参数）——**取决于岔路①②拍板**，可 Phase 2 / 可裁剪。
4. **clock DI**（同动构造签名，`app.state.clock` seam + 默认 `datetime.now(UTC)`）。
5. **keystone L3 e2e**：脚本化 adapter 驱动真决策环走完一次 tool_call 派发（§4）。
6. **文档漂移修**：e2e-testing.md DI 清单诚实化 + testing-strategy.md TestModel/FunctionModel 从"愿景"改"已落地"。

### 1.3 Out of scope（显式退出）

- **wire 级 mock**（`httpx.MockTransport` / `provider_router` http_client 注入）→ F139 VCR 域（research §5.5）。
- **`ALLOW_MODEL_REQUESTS` 硬闸** → F137 门禁止血（F138 keystone 测试是它第一个受益者，但闸本身 F137 建）。
- **73 处 `datetime.now` 全量 clock 化** → F142 确定性护栏基篮（F138 只加 seam + ≤1 个 demonstrating consumer）。
- **把现有 L2 用例批量迁到 L3** → F138 只交付地基 + keystone 锚 + 2-3 个示范样例，批量迁移各 Feature 自取。
- **改任何生产决策逻辑**（Constitution #9：脚本化只在测试层）。

---

## 2. 三条设计岔路（回用户拍板 + 推荐）

### 岔路① SchemaTestAdapter 放哪：provider 包（随包公开 API）vs test-only

**背景**：pydantic-ai 把 TestModel/FunctionModel 作为 **production 公开 API** 随包发布。但 OctoAgent 有两点不同：①脚本件实现的是 **`StructuredModelClientProtocol`（skills 包协议）**，不是 provider 的 `MessageAdapter`——放 provider 包并非自然归属；②Constitution #9（脚本化不得进生产决策）。

**选项：**
- **A. provider 包公开 API**：随包发布，任何 import 可达。仿 pydantic-ai。风险：生产路径可 import，#9 边界靠约定守。
- **B. test-only（tests/ 或 conftest）**：只在测试可见。风险：OctoBench runner（`benchmarks/`）、e2e_live、L4 单测**跨包三方消费**时无法共享（QueueModelClient 现在的困境就是这个）。
- **C.【推荐】skills 包内 `testing` 子模块**（`packages/skills/src/octoagent/skills/testing/scripted_model.py`）：**随包发布但命名空间明示 testing**，仿 pydantic-ai `pydantic_ai/models/test.py`（可 import 但生产从不 wire）。

**推荐 C**，理由：①协议在 skills 包，`testing` 子模块是自然归属；②可发布→三方消费者（OctoBench/e2e_live/L4）零障碍 import，解决 QueueModelClient 上提的根本诉求；③`testing.` 命名空间 + harness DI 默认 None（生产不传）= 构造性守住 #9，比"放 provider 靠约定"更硬；④与 SDK conformance suite 随包发布（claude-agent-sdk 范式）一致。**子决策（岔路①附带）**：SchemaTestAdapter 用 pydantic-ai 私有 `_JsonSchemaTestData`（零维护但私有 API 脆，须配库签名锁测试）vs 自写极简 schema-walker（+80 行、零私有依赖）→ **推荐私有复用 + 签名锁测试**（F110 教训机制化，成本 1 个锁测试 << 80 行自写维护）。

### 岔路② 可编程脚本脑（FunctionModel 式）vs 单步 schema 自动填参（TestModel 默认）

**背景**：两者对应 pydantic-ai 的 FunctionModel（本地函数按序当脑）vs TestModel（按 schema 默认调全工具）。**关键事实**：FunctionModel 等价物 `QueueModelClient` **已经写好且测过**（研究 §5.1），schema-fill 的 SchemaTestAdapter 是新工。

**选项：**
- **A. 只做单步 schema 自动填参**（原岔路描述的"先只做"）：SchemaTestAdapter 一步返回按 schema 填的 tool_calls。够测"schema 合法性 + 派发通路"，但**填的参数是 seed 随机合法值**（如 `user_profile.update` 一个随机字符串），回写内容无业务意义、多步链无法编排。
- **B.【推荐】可编程脚本脑优先（QueueModelClient 上提），SchemaTestAdapter 作为 Phase 2 便利层**：先上提 `QueueModelClient`（≈0 成本、已验证）作为 keystone 主力——它能精确编排"第 1 轮调 A、第 2 轮调 B、第 3 轮返 complete"的**多步决策环**，参数由测试显式给（有业务意义，能断言真实回写）。SchemaTestAdapter 之后补，专供"扫 63 工具 schema 广度"的批量降层。

**推荐 B**，理由：①keystone 的验收锚是"驱动真决策环走完 tool_call 派发并断言真实回写"——需要**可控的参数 + 可控的多步链**，正是可编程脚本脑，**而非** seed 随机单步；②`QueueModelClient` 已存在已验证，是最短 keystone 路径（**先交付能跑的地基**）；③SchemaTestAdapter 的价值（免逐工具写脚本、扫全 63 工具）是**广度便利**，不是 keystone 必需，作 Phase 2 降低首版风险面。注意这**反转**了岔路描述里"先只做单步"的倾向——因为可编程脚本脑其实更便宜（已写好）且更贴 keystone。

### 岔路③ 与 EchoMessageAdapter：替换 vs 并存

**背景**：`EchoMessageAdapter` 在**路径 A**（FallbackManager，纯文本补全），新脚本件在**路径 B**（SkillRunner 决策环）——**是两个层、两个协议、两个对象**，本质不冲突。

**选项：**
- **A. 替换**：语义上无意义——Echo 实现 `MessageAdapter`（文本），脚本件实现 `StructuredModelClientProtocol`（结构化 tool_calls），不可互换；且 22 个 L3 Echo 全栈测试依赖 Echo 的纯文本降级。
- **B.【推荐】并存**：Echo 保留服务路径 A（纯文本 / 无工具兜底 / FallbackManager fallback），脚本件新增服务路径 B（决策环）。零回归。

**推荐 B（并存）**，理由：正交对象，替换是伪命题；22 Echo 测试零触碰。**但有一个必须一起拍的子决策**：新 `model_client` DI **不能被 echo-skip 门挡住**（`octo_harness.py:1134`）——设计为"`model_client` override 非 None 时无条件构造 SkillRunner（不看 `_llm_mode_env`）"，即 override 存在即打通决策环，与 llm_mode 解耦（见 §3 wiring）。

---

## 3. 设计（wiring）

### 3.1 harness 构造签名（model_client + clock 同动）

```python
# octo_harness.py OctoHarness.__init__（现 :123-132）新增两参
def __init__(self, project_root, *, credential_store=None, llm_adapter=None,
             mcp_servers_dir=None, data_dir=None, plugins_dir=None,
             model_client=None,          # 新增：StructuredModelClientProtocol | None
             clock=None):                # 新增：Callable[[], datetime] | None
    ...
    self._model_client_override = model_client
    self._clock_override = clock
```

### 3.2 SkillRunner 注入点（拦截硬连，byte-for-byte None 等价）

```python
# octo_harness.py _bootstrap_executors（现 :1134-1157）
if self._model_client_override is not None:
    # F138：脚本化决策环——override 存在即无条件建 SkillRunner（与 llm_mode 解耦，岔路③子决策）
    _model_client = self._model_client_override
    skill_runner = SkillRunner(model_client=_model_client, tool_broker=tool_broker, ...)
    app.state.llm_service = LLMService(..., skill_runner=skill_runner, ...)
elif _llm_mode_env != "echo":
    skill_runner = SkillRunner(model_client=ProviderModelClient(...), ...)   # 生产原路，零改动
    ...
else:
    _log.info("skill_runner_skipped", reason="echo_mode")                     # echo 原路，零改动
```
**不变量**：`model_client is None AND llm_mode != echo` → 与 master 逐行等价（生产路径 `main.py:425` 不传 → 恒 None）。

### 3.3 clock seam（最小）

- `app.state.clock = self._clock_override or (lambda: datetime.now(UTC))`（bootstrap 一处赋值）。
- demonstrating consumer（岔路③外的次要决策，建议纳入）：watchdog `detectors/cooldown/scanner` 的 `datetime.now(UTC)` 改读 `app.state.clock`（≤5 处），坐实"clock 注入的确定性时间测试本可在 L4 抓住 F103d watchdog bug"。**其余 68 处不动**（F142）。

### 3.4 脚本化件（岔路①②拍板后定型）

- `ScriptedModelClient`（QueueModelClient 上提+改名）：`packages/skills/src/octoagent/skills/testing/scripted_model.py`，实现 `generate` 按队列返回预置 `SkillOutputEnvelope`。
- `SchemaTestAdapter`（Phase 2，若拍板做）：同模块，`generate` 用 `tool_broker.discover()` + `_JsonSchemaTestData(tool_meta.parameters_json_schema, seed).generate()` 组装 tool_calls；配 `test_schema_test_data_api_lock.py`（sys.modules 断言 pydantic-ai 私有符号签名未变，F110 范式）。
- 上提后 `packages/skills/tests/conftest.py:80-104` 的 `QueueModelClient` 改为 re-export（保 test_runner.py 零改）。

---

## 4. Keystone 验收锚（L3 e2e 样例设计）

**测试**：`apps/gateway/tests/e2e_live/test_e2e_scripted_decision_loop.py`（新 marker `e2e_scripted`，或纳入 e2e_smoke——见 §6 决策）

```python
async def test_scripted_adapter_drives_real_decision_loop(scripted_harness):
    # 1. harness 带脚本脑 bootstrap 全 11 段（真 store / 真 tool_broker / 真 SnapshotStore，
    #    credential_store=fake（无需真 OAuth → CI 可跑！），model_client=ScriptedModelClient([
    #      SkillOutputEnvelope(tool_calls=[ToolCallSpec("user_profile.update", {偏好写入})]),
    #      SkillOutputEnvelope(content="已记录", complete=True),
    #    ]))
    # 2. 驱动真决策环（keystone MVP 走 direct 入口）：
    result = await app.state.llm_service.call(
        "记住我喜欢简洁回复", task_id=tid, trace_id=tid,
        metadata={"selected_tools": ["user_profile.update"]})
    # 3. 断言（≥2 独立断言点，spec 房规）：
    assert scripted.calls >= 1                              # 决策环真跑了（脚本脑被消费）
    assert ("user_profile.update", ...) in tool_broker_calls # 决策→派发真发生（前半段！）
    assert "简洁" in USER_md_content                          # 回写真落盘（后半段）
    assert MEMORY_ENTRY_ADDED in events                     # 事件真产
    assert no_real_provider_http_call                       # 零真 LLM（F137 硬闸兜底后更强）
```

**为什么这是 keystone**：它与 `test_e2e_basic_tool_context.py` 的**唯一差别**——后者从 `tool_broker.execute()` 切进（跳过决策），本测试让**脚本化 LLM 决定**调 `user_profile.update`，**完整跑决策环前半段**。这就是 L3 此前零覆盖的那一跳。

**附加价值（CI 可跑）**：脚本化路径**不需要真 OAuth**（不打 provider）→ 摆脱 e2e_smoke 的 `real_codex_credential_store` 宿主依赖 → **可进干净 CI**（补 M9 "CI 断链、L2/L3 只能宿主机跑" 的洞）。这是 keystone 之外的战略红利。

---

## 5. AC（验收标准）

| AC | 断言 | 绑定测试（SDD AC↔test 显式绑定）|
|----|------|--------------------------------|
| AC-1 | `OctoHarness(model_client=X)` 时 SkillRunner 用 X（不用 ProviderModelClient），且**与 llm_mode 解耦**（echo 模式也建 SkillRunner）| `test_octo_harness_model_client_di.py` |
| AC-2 | `model_client=None AND clock=None` 时构造与 master byte-for-byte 等价（生产零影响）| `test_octo_harness_di_none_equivalence.py`（对账 bootstrap 产物）|
| AC-3【keystone】| 脚本化 adapter 驱动真 SkillRunner → 真 tool_broker.execute → 真回写（USER.md + MEMORY_ENTRY_ADDED），零真 provider HTTP | `test_e2e_scripted_decision_loop.py` |
| AC-4 | `ScriptedModelClient` 多步链：第 1 轮 tool_call A、第 2 轮 tool_call B、第 3 轮 complete，决策环按序消费 | `test_scripted_model_multistep.py` |
| AC-5 | `QueueModelClient` 上提后 `test_runner.py` 全套零改动通过（re-export 兼容）| `packages/skills/tests/test_runner.py`（既有）|
| AC-6 | clock DI：注入固定时钟后 watchdog 时间判断确定性可测（F103d offset-naive 类 bug 在 L4 可抓）| `test_watchdog_clock_di.py` |
| AC-7【若拍板做】| SchemaTestAdapter 按 `parameters_json_schema` 生成合法参数 + 私有 API 签名锁 | `test_schema_test_adapter.py` + `test_schema_test_data_api_lock.py` |
| AC-8 | keystone L3 测试**不依赖宿主 OAuth**（fake credential_store 即可跑）→ CI-runnable | `test_e2e_scripted_decision_loop.py`（fake cred fixture）|
| AC-9 | 0 regression vs master `8fb1386e`（≥ 全量 baseline passed，e2e_smoke 8/8）| 全量回归 |

---

## 6. 待定 / 次要决策（可主 session 定，不必回用户）

- **新 marker `e2e_scripted` vs 纳入 `e2e_smoke`**：倾向新 marker——keystone 测试无 OAuth 依赖、可进 CI，语义上是"确定性决策环"独立层；纳入 e2e_smoke 会继承其宿主 OAuth SKIP 逻辑。**建议新 marker**，F141 三模式 lane 时归入 pr lane（每次都跑）。
- **clock 是否接 watchdog**：建议接（≤5 处，坐实 bug 价值）；若求最小面可只留 `app.state.clock` seam。
- **direct 入口 vs /api/message 全链路**：keystone MVP 走 direct（`llm_service.call`）；/api/message 全链路（含 TaskService 编排 + selected_tools 上游填充）作 Phase 2 广度样例。

---

## 7. 宪法自查（Constitution）

| # | 条款 | F138 边界 |
|---|------|----------|
| #9 | 禁硬编码替代 LLM 决策 | **脚本化只在测试层**：`testing` 子模块 + harness DI 默认 None + 生产 `main.py` 不传 → 生产决策环恒用真 `ProviderModelClient`。构造性不可达，非约定。 |
| #6 | Degrade Gracefully | DI 全 None 生产等价；脚本件缺失不影响生产。 |
| #3 | Tools are Contracts | SchemaTestAdapter 复用 `parameters_json_schema` 单一事实源，不另造 schema。 |
| #2 | Everything is an Event | 脚本化决策环产的事件（MODEL_STARTED/COMPLETED、TOOL_*、MEMORY_ENTRY_ADDED）与真 LLM 路径同链路，L3 可断言事件链。 |

**双评审要求**（重大架构变更：touches harness 核心 + 新 provider/skills 测试件）：Codex + Opus 双评审 0 HIGH 后再合。

---

## 8. 规模复核

原估 **L**，复核**维持 L**（若 SchemaTestAdapter 裁到 Phase 2 则首版 **M-L**）：
- model_client DI + 拦截硬连 + None 等价对账：**M**（harness bootstrap 核心，须严守 byte-for-byte）
- QueueModelClient 上提 + re-export：**S**（已存在）
- clock DI seam + watchdog consumer：**S-M**
- keystone L3 e2e + 多步样例 + fake-cred fixture：**M**
- SchemaTestAdapter + 私有 API 签名锁：**M**（可 Phase 2）
- 文档漂移修（e2e-testing.md / testing-strategy.md）：**S**
- 双评审 + None 等价对账的 rigor 开销：叠加
