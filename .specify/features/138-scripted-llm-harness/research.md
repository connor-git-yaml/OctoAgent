# F138 — 脚本化 LLM harness · Research（决策环取证）

> M9 P0 keystone · L3 确定性覆盖 agent 决策环 · 规模 L
> 全部 file:line 以 worktree `feature/138-scripted-llm-harness`（off master `8fb1386e`）实读核实（行号会漂，本文档所有引用均已 grep 复核）。

---

## 0. 一句话结论

L3（确定性 E2E，不打真 LLM）现在**测不了 agent 决策环的前半段**（"LLM 决定调哪个工具"）。原因不是测得少，是**缺一个能产 `tool_calls` 的脚本化 `model_client`**，且 `SkillRunner` 的 `model_client` 在 harness 里**硬连** `ProviderModelClient`、**无注入点**。现成料 `QueueModelClient` 已证明这条协议可脚本化，但埋在 `packages/skills/tests` 从未上提。补一个 harness 级 `model_client` DI + 上提脚本化 client 即打通。

---

## 1. 两条 LLM 路径（为什么 `llm_adapter` 替不到决策环）

OctoAgent 有**两条互相独立**的 LLM 调用路径，共用 `LLMService` 入口但走不同对象：

| 路径 | 对象 | 产 tool_calls？ | harness DI | 入口 |
|------|------|----------------|-----------|------|
| **A. FallbackManager.primary**（纯文本补全 / 无工具兜底）| `MessageAdapter` | ❌ 否 | ✅ `llm_adapter`（已有）| `LLMService.call → _fallback_manager.call_with_fallback` |
| **B. SkillRunner 决策环**（agent 决策→工具派发）| `StructuredModelClientProtocol` | ✅ 是 | ❌ **无**（硬连 `ProviderModelClient`）| `LLMService.call → _try_call_with_tools → skill_runner.run → model_client.generate` |

**关键取证：**

- `LLMService.call`（`apps/gateway/src/octoagent/gateway/services/llm_service.py:278`）**先**试 `_try_call_with_tools`（:314），返回 None 才落到 `_fallback_manager.call_with_fallback`（:327）。
- `_try_call_with_tools`（:333）门槛：`skill_runner is not None AND task_id AND trace_id AND selected_tools`（:345-350），满足则构造 `SkillManifest(tools_allowed=selected_tools)`（:405-412）→ `skill_runner.run(manifest, execution_context, skill_input={"objective": prompt}, prompt=prompt)`（:446）。
- **`llm_adapter` DI 只替 A**：`octo_harness.py:747-757` `if self._llm_adapter_override is not None:` → `FallbackManager(primary=self._llm_adapter_override, fallback=EchoMessageAdapter())` → `LLMService(fallback_manager=...)`。它注入的是 `FallbackManager` 的 primary，**跟 SkillRunner 的 `model_client` 是两个完全不同的对象**。
- **`SkillRunner` 的 `model_client` 硬连**：`octo_harness.py:1136-1141`
  ```python
  skill_runner = SkillRunner(
      model_client=ProviderModelClient(          # ← 硬连，无 DI 参数
          provider_router=app.state.provider_router,
          tool_broker=tool_broker,
          event_store=store_group.event_store,
      ),
      ...
  )
  ```
  `OctoHarness.__init__`（:123-143）只有 5 个 DI：`credential_store / llm_adapter / mcp_servers_dir / data_dir / plugins_dir`——**没有 `model_client`**（也没有 `clock`）。

---

## 2. 为什么现有离线件全部覆盖不到决策环

| 离线件 | 位置 | 死因 |
|--------|------|------|
| `EchoMessageAdapter` | `packages/provider/src/octoagent/provider/echo_adapter.py` | 只回显文本，**永不产 tool_calls**；且它活在**路径 A**（FallbackManager），不进 SkillRunner |
| `OCTOAGENT_LLM_MODE=echo` | `octo_harness.py:1134,1155-1157` | echo 模式下 `if _llm_mode_env != "echo":` 为假 → **SkillRunner 整个被跳过**（`skill_runner_skipped`）→ 决策环**不存在** |

→ 没有任何办法让真 `SkillRunner` 带一个"确定性、不打真 LLM"的 `model_client` 跑起来。

---

## 3. L3 现在怎么"绕过"决策环（keystone gap 实证）

`apps/gateway/tests/e2e_live/test_e2e_basic_tool_context.py`（e2e_smoke，pre-commit 自动跑）docstring 明写（:1-24）：

> 2. **真走 `app.state.tool_broker.execute(...)` 路径**……不再绕过 broker 直调 handler
> 3. **不真打 Codex OAuth LLM**……避免被 LLM 真实响应不确定性卡住

即：case 直接 `tool_broker.execute("user_profile.update", add)`（:20 注释 T-P3-1），**跳过 `model_client.generate`**。它验证了决策环的**后半段**（工具派发→回写：WriteResult / MEMORY_ENTRY_ADDED / USER.md），但**从不触发前半段**（LLM 决定要调这个工具）。63 工具的"决策→派发"这一跳在 L3 **零覆盖**。

**决策环完整链路**（`packages/skills/src/octoagent/skills/runner.py`）：
```
run() while tracker.check_limits() is None:            # :140 多步循环
  raw_output = await self._model_client.generate(...)  # :149 ← 决策（脚本化注入点）
  if output.tool_calls:                                 # :373
    tool_feedbacks = await self._execute_tool_calls(   # :375 → def :511
        tool_calls=output.tool_calls)                   #        分桶 SideEffectLevel → tool_broker.execute()
    feedback.extend(tool_feedbacks)                      # :407 → 回灌下一轮 generate(feedback=...)
  if output.complete: ...                                # 终止
```
`test_e2e_basic_tool_context.py` 从 `tool_broker.execute()` 那一层切进去，`run()` / `generate()` / 循环全不跑。

---

## 4. 协议契约：脚本化 `model_client` 需要满足什么

`StructuredModelClientProtocol`（`packages/skills/src/octoagent/skills/protocols.py:19-32`）**单方法**：
```python
async def generate(self, *, manifest, execution_context, prompt,
                   feedback, attempt, step) -> SkillOutputEnvelope: ...
```
`SkillOutputEnvelope`（`packages/skills/src/octoagent/skills/models.py:308-318`）：`content / complete / skip_remaining_tools / tool_calls: list[ToolCallSpec] / metadata / token_usage / cost_usd`。
`ToolCallSpec`（:292-305）：`tool_name / arguments: dict / tool_call_id`。

**runner 对可选成员的宽容**（决定了脚本件多简单能过）：
- `clear_history`：`getattr(self._model_client, "clear_history", None)`（runner.py:755）→ **可选**，无则 no-op。
- `token_usage` / `cost_usd`：`hasattr(raw_output, ...)`（runner.py:167,174）→ **可选**，envelope 默认值即可。

→ 一个脚本件**只需实现 `generate()` 返回预置 envelope**，即可驱动完整多步决策环。

---

## 5. 现成料复用 vs 重造判断

### 5.1 `QueueModelClient`（FunctionModel 等价，**复用·上提**）

`packages/skills/tests/conftest.py:80-104`（~24 行）：
```python
class QueueModelClient:
    def __init__(self, items: list[SkillOutputEnvelope | Exception]): self._queue = deque(items); self.calls = 0
    async def generate(self, *, manifest, execution_context, prompt, feedback, attempt, step) -> SkillOutputEnvelope:
        self.calls += 1
        if not self._queue: return SkillOutputEnvelope(content="default", complete=True)
        item = self._queue.popleft()
        if isinstance(item, Exception): raise item
        return item
```
- **无状态**——忽略 `prompt/feedback/manifest`，按 deque 顺序返回预置 envelope（可含 tool_calls，可抛异常）。
- **已被 `packages/skills/tests/test_runner.py` 全套消费**，证明能驱动 SkillRunner 多步循环。
- 它就是**"按序返回预设 tool_call 链驱动多步决策环"**（第 1 轮调 A、第 2 轮调 B、第 3 轮返文本）的 FunctionModel 式脚本脑——设计岔路②的一半**其实已经写好了**，只是从未上提为共享件 / 接入 OctoHarness。
- **判断**：复用，上提到可发布的 `testing` 子模块（不留在 tests/）。

### 5.2 `reflect_tool_schema` + `parameters_json_schema`（SchemaTestAdapter 原料，**复用**）

- `packages/tooling/src/octoagent/tooling/schema.py:69,122`：`ToolMeta.parameters_json_schema`（:122）是**单一事实源**，由 `pydantic_ai._function_schema.function_schema()`（:105）生成。
- 决策环取 schema 的既有路径：`ProviderModelClient._get_tool_schemas`（provider_model_client.py:255-294）→ `tool_broker.discover()`（:272）拿全部 ToolMeta → 每个 `tool_meta.parameters_json_schema`（:291）。SchemaTestAdapter 复用同一路径。

### 5.3 pydantic-ai `_JsonSchemaTestData`（SchemaTestAdapter 填参引擎，**可复用·但有私有 API 风险**）

- `_references/opensource/pydantic-ai/.../models/test.py`：`TestModel.gen_tool_args` = `_JsonSchemaTestData(tool_def.parameters_json_schema, self.seed).generate()`（:180,404）。按 JSON schema + seed 确定性生成合法参数（enum/examples/anyOf/数值范围/日期/字符串全覆盖，:419-553）。
- **已实测可导入**：项目 venv `pydantic_ai==1.63.0`，`from pydantic_ai.models.test import _JsonSchemaTestData` OK。我们的 `parameters_json_schema` 形状与 `tool_def.parameters_json_schema` 一致 → 可直接喂。
- **风险**：`_JsonSchemaTestData` 是**私有符号**（前导下划线），pydantic-ai 升级可能改。若采用须配**库 API 签名锁测试**（F110 piper `synthesize_wav` 教训：hermetic Fake 会掩盖真库 API 错用）。替代：自写极简 schema-walker（多 ~80 行，零私有依赖）。→ 见 spec 设计岔路①子决策。

### 5.4 `llm_adapter` DI 缝（**模式借鉴·对象错位**）

`octo_harness.py:128,140,747-757` 是 DI 缝的**范式**（≈ pydantic-ai `Agent.override`），但注入的是 `FallbackManager`（路径 A），**不是** SkillRunner 的 `model_client`（路径 B）。F138 需**新增**一个 `model_client` DI，不能复用 `llm_adapter`。

### 5.5 wire 级替代方案（`httpx.MockTransport`，**不推荐做 keystone**）

`provider_router.py:81` 硬编码 `self._http = httpx.AsyncClient(...)`，无 `http_client/transport` 注入参数（`ProviderClient` 本身接受 `http_client` 但只被 `_build_client` 内部构造，:206）。理论上可在 wire 层脚本化 provider 响应，但：①要构造真 provider 的 HTTP wire body（OpenAI Chat / Responses / Anthropic Messages 三 transport 各不同），远比在 protocol 层脚本化 envelope 贵；②偏离 keystone"确定性驱动决策环"的最短路径。→ wire 级 mock 归 F139 VCR 域，F138 只做 protocol 层。

---

## 6. clock DI（同动 harness 构造签名，避免和 F142 抢文件）

- `datetime.now` 在 gateway 层散布 **73 文件**（grep 计数）——clock 是隐式依赖。
- F103d 真跑暴露的 watchdog datetime offset-naive 比较 bug（`3eabd58`）是这缝的实证；bug locus：`services/watchdog/{detectors,cooldown,scanner}.py`（各 `datetime.now(UTC)` + `tzinfo is None` 补丁，detectors.py:87,121-122,167,170-171,227 等）。
- **F138 clock DI 范围**：只加 harness 构造函数**注入点** + 一个明确 seam（`app.state.clock`，默认 `lambda: datetime.now(UTC)`），**不**重构 73 处调用点（那是 F142 确定性护栏基篮）。是否顺手把 watchdog 一个 demonstrating consumer 接上 clock，见 spec 设计岔路（次要）。
- 放 F138 的**唯一理由**：与 `model_client` DI 一起改 `OctoHarness.__init__` 签名，避免 F142 和本 Feature 抢同一构造函数文件产生 rebase 冲突。

---

## 7. 生产零影响的构造性保证

`main.py:425` 生产路径 `OctoHarness(project_root=_resolve_project_root())`——**不传** `credential_store/llm_adapter/model_client/clock`，全部走 None 默认。只要新 DI 默认 None 且 None 分支保留 `ProviderModelClient(...)` 原构造，即 **byte-for-byte 等价**（沿用 F087 T-P2-8 "任一 override 全 None 时生产路径行为不变" 范式）。这是 Constitution #6（Degrade Gracefully）+ #9（脚本化只在测试层、生产不可达）的落点。

---

## 8. 竞品对标（pydantic-ai，测试成熟度天花板）

| 机制 | pydantic-ai | F138 对应 |
|------|-------------|----------|
| `TestModel`（按 schema 默认调全工具 + `_JsonSchemaTestData` seed 填参）| production 公开 API，`models/test.py:62,180,404` | **SchemaTestAdapter**（岔路①②的 schema-fill 半边）|
| `FunctionModel`（本地函数当模型脑，按序脚本）| production 公开 API，`models/function.py:45-56` | **`QueueModelClient` 上提**（岔路②的可编程脚本脑半边，**已存在**）|
| `Agent.override()`（任意调用点换 model/deps）| `agent/abstract.py:1480` | **harness `model_client` DI**（本 Feature 主产出）|
| `ALLOW_MODEL_REQUESTS` 硬闸（漏网真调用炸）| `models/__init__.py:901` | **F137** 域（F138 的 keystone 测试是它的第一个受益者：不打真 LLM 却跑完决策环）|

启示：pydantic-ai 把"L2 真 LLM 用例"整体**降层**到 L3 成本（TestModel/FunctionModel 确定性驱动 agent loop）。F138 就是给 OctoAgent 装这个降层地基。
