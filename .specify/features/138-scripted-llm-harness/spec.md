# F138 — 脚本化 LLM harness（L3 决策环确定性覆盖）

> M9 P0 **keystone** · 规模 **M-L**（SchemaTestAdapter 裁到 Phase 2 后）· 分支 `feature/138-scripted-llm-harness`（rebase onto master `a1e4ca15`）
> **v2 收窄版（2026-07-11）**：三设计岔路已拍板（用户拍板②，主节点按 agent 推荐①③，Fable 5 复审维持），本版写死拍板结果、删除岔路讨论态、补 pre-commit hook 陷阱防御设计。实施后双评审 0 HIGH，**不 push 等拍板**。
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
2. **`QueueModelClient` 上提**为可发布的 `octoagent.skills.testing` 子模块共享件，改名 `ScriptedModelClient`（拍板②的可编程脚本脑，已存在）。
3. **clock DI**（同动构造签名，`app.state.clock` seam + 默认 `datetime.now(UTC)` + watchdog 单一 demonstrating consumer 子系统）。
4. **keystone L3 e2e**：脚本化 adapter 驱动真决策环走完一次 tool_call 派发（§4）。
5. **文档漂移修**：e2e-testing.md DI 清单诚实化 + testing-strategy.md TestModel/FunctionModel 从"愿景"改"已落地"。

### 1.3 Out of scope（显式退出）

- **`SchemaTestAdapter`**（TestModel 等价：按工具 schema 自动填合法参数）→ **Phase 2 deferred**（拍板②，见 §2.2 deferred 理由与范围）。
- **wire 级 mock**（`httpx.MockTransport` / `provider_router` http_client 注入）→ F139 VCR 域（research §5.5）。
- **`ALLOW_MODEL_REQUESTS` 硬闸** → F137 门禁止血（F138 keystone 测试是它第一个受益者，但闸本身 F137 建）。
- **73 处 `datetime.now` 全量 clock 化** → F142 确定性护栏基篮（F138 只加 seam + watchdog 一个 demonstrating consumer 子系统，实测 6 个调用点，见 §3.3）。
- **把现有 L2 用例批量迁到 L3** → F138 只交付地基 + keystone 锚 + 示范样例，批量迁移各 Feature 自取。
- **改任何生产决策逻辑**（Constitution #9：脚本化只在测试层）。

---

## 2. 三条设计岔路——拍板决议（已收窄，写死）

> 拍板来源：主 session 六岔路拍板（2026-07-09，②为用户拍板，①③为主节点按 agent 推荐）+ Fable 5 复审维持（CLAUDE.local.md「首波设计先行完成 + 六岔路拍板」节）。原岔路选项对比论证见 git 历史 `ee0ba50b` 版 spec §2。

### 2.1 拍板①：脚本件放 skills 包 `testing` 子模块（随包发布公开 API）

`packages/skills/src/octoagent/skills/testing/`（`scripted_model.py` + `__init__.py`）。**随包发布但命名空间明示 testing**，仿 pydantic-ai `pydantic_ai/models/test.py`（可 import 但生产从不 wire）。

理由（保留，Constitution #9 自查依据）：①脚本件实现的是 `StructuredModelClientProtocol`（skills 包协议），`testing` 子模块是自然归属；②可发布 → 三方消费者（OctoBench / e2e_live / L4 单测）零障碍 import，解决 QueueModelClient 埋在 `packages/skills/tests/conftest.py` 无法跨包共享的根本困境；③**#9 构造性不可达**：`testing.` 命名空间 + harness DI 默认 None + 生产 `main.py:425` 只传 `project_root` 不传任何 override → 生产决策环恒用真 `ProviderModelClient`，脚本化路径在生产**构造性不可达**（非约定守卫）；④与 SDK conformance suite 随包发布（claude-agent-sdk 范式）一致。

**不引重依赖**：`testing` 子模块只 import skills 包自身模型（`SkillOutputEnvelope` / `SkillManifest` / `SkillExecutionContext`）+ 标准库 `collections.deque`，零新增第三方依赖。

### 2.2 拍板②（用户拍板）：脚本脑优先，SchemaTestAdapter → Phase 2 deferred

首版**只**上提现成 `QueueModelClient`（`packages/skills/tests/conftest.py:80-104`，~24 行已验证）为 `octoagent.skills.testing.ScriptedModelClient`——它能精确编排"第 1 轮调 A、第 2 轮调 B、第 3 轮返 complete"的多步决策环，参数由测试显式给（有业务意义，能断言真实回写），是最短 keystone 路径、首版风险最小。conftest 改 re-export 别名（`QueueModelClient = ScriptedModelClient`），`test_runner.py` 等既有消费者零改动。

**SchemaTestAdapter deferred（Phase 2，本次不实施）**：
- **范围**：同模块加 `SchemaTestAdapter`——`generate` 用 `tool_broker.discover()` 拿全部 ToolMeta，每个 `tool_meta.parameters_json_schema` 喂 pydantic-ai 私有 `_JsonSchemaTestData(schema, seed).generate()` 确定性生成合法参数组装 tool_calls；配 `test_schema_test_data_api_lock.py` 私有 API 签名锁（F110 piper 教训机制化）。
- **deferred 理由**：①它的价值是"免逐工具写脚本、扫全 63 工具 schema 广度"的**批量降层便利**，不是 keystone（驱动真决策环 + 断言真实回写）必需——keystone 需要**可控参数 + 可控多步链**，正是脚本脑而非 seed 随机单步；②私有 API 依赖（`_JsonSchemaTestData`）带升级漂移风险面，首版不背；③裁掉后规模从 L 收到 M-L，守住首波并行（F137∥F138）的交付节奏。
- **启动条件**：F138 合入后任意时点可作独立 followup（地基 DI 缝已就位，纯增量）。

### 2.3 拍板③：与 EchoMessageAdapter 并存 + override 与 llm_mode 解耦

Echo 保留服务**路径 A**（FallbackManager 纯文本补全 / 无工具兜底），脚本件新增服务**路径 B**（SkillRunner 决策环）。两个层、两个协议、两个对象，替换是伪命题；**22 个 Echo L3 全栈测试零触碰**。

**关键子决策（一起拍定）**：新 `model_client` DI **不被 echo-skip 门挡住**（`octo_harness.py:1134`）——`model_client` override 非 None 时**无条件**构造 SkillRunner（不看 `_llm_mode_env`），即 override 存在即打通决策环，与 llm_mode 解耦（见 §3 wiring）。同时 override 模式下 bootstrap **不得因缺真实凭证而失败**——这是"脚本路径不需真 OAuth → 可进干净 CI"的关键（实测支撑：ProviderRouter 构造本就不校验凭证，echo 模式 baseline 已在无凭证下构造它；override 分支不构造 `ProviderModelClient`，无新增凭证依赖）。

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

### 3.2 SkillRunner 注入点（拦截硬连，None 行为等价 + 最小 diff 形状）

```python
# octo_harness.py _bootstrap_executors（现 :1134-1157）
if self._model_client_override is not None:
    # F138：脚本化决策环——override 存在即无条件建 SkillRunner（与 llm_mode 解耦，拍板③子决策）。
    # 除 model_client 换成 override 外，SkillRunner / LLMService 构造与非 echo 分支完全同构
    # （同 hooks=[AgentSessionTurnHook] / 同 on_tool_search_result / 同 skill_discovery /
    #  同 _llm_service_ref.append + AgentContextService.set_llm_service）。
    skill_runner = SkillRunner(model_client=self._model_client_override, tool_broker=tool_broker, ...)
    app.state.llm_service = LLMService(..., skill_runner=skill_runner, ...)
elif _llm_mode_env != "echo":                       # ← 原 if 改 elif，块体逐行不动
    skill_runner = SkillRunner(model_client=ProviderModelClient(...), ...)   # 生产原路，零改动
    ...
else:
    _log.info("skill_runner_skipped", reason="echo_mode")                     # echo 原路，零改动
```

**None 等价语义（精确化，替代口语化的 byte-for-byte）**：
1. `model_client is None` 时控制流与 master 逐行等价——diff 形状锁定为"原 `if _llm_mode_env != "echo":` 行改 `elif`，块体零改动"（生产路径 `main.py:425` 不传 → 恒 None）。
2. `clock is None` 时新增的 `app.state.clock` 是 **additive inert seam**：赋默认 `datetime.now(UTC)` 等价 callable；全库现存零消费者读 `app.state.clock`（grep 实证），watchdog 消费点注入默认 clock 后计算逐值等价。即：**新增一个无人读的 state attr + 既有行为零变更**——这是与 F087 T-P2-8 "任一 override 全 None 时生产路径行为不变"同款承诺，不宣称 app.state 字面 byte 相等。
3. 终局证据链：AC-2 对账测试 + 全量回归 0 regression（baseline 4919 passed）+ diff 形状 review。

### 3.3 clock seam（最小）

- `bootstrap()` 入口一处赋值：`app.state.clock = self._clock_override or _default_clock`（`_default_clock = lambda: datetime.now(UTC)`，app 级 seam，不属任何 domain 段）。
- **demonstrating consumer = watchdog 子系统（单一消费者，实测 6 个调用点）**：`detectors.py:87,167,227` + `cooldown.py:44,79` + `scanner.py:201` 的 `datetime.now(UTC)` 改读构造注入的 clock（构造参数 `clock: Callable[[], datetime] | None = None`，None 默认 `datetime.now(UTC)` 行为等价；harness `_bootstrap_optional_routines` 传入与 `app.state.clock` 同一 callable）。坐实"clock 注入的确定性时间测试本可在 L4 抓住 F103d watchdog offset-naive bug"（AC-6）。**其余 ~67 处 `datetime.now` 不动**（F142）。
- 选构造注入而非服务内读 `app.state`：watchdog 各件不持 app 引用，构造注入零耦合、L4 可独测。

### 3.4 脚本化件（拍板①②定型）

- `ScriptedModelClient`（QueueModelClient 上提+改名）：`packages/skills/src/octoagent/skills/testing/scripted_model.py`，实现 `generate` 按 deque 队列返回预置 `SkillOutputEnvelope`（或 raise 预置 Exception），队列耗尽返回 `SkillOutputEnvelope(content="default", complete=True)`；`calls` 计数器供断言。**实现逻辑零变更**（仅上提+改名）。
- `SchemaTestAdapter`：**Phase 2 deferred**（§2.2）。
- 上提后 `packages/skills/tests/conftest.py:80-104` 的 `QueueModelClient` 改为 re-export 别名（`from octoagent.skills.testing import ScriptedModelClient as QueueModelClient`），保 `test_runner.py` 等 8 个既有消费文件零改动。

### 3.5 pre-commit hook 陷阱防御（pre-merge 窗口，实施硬约束）

**陷阱**（memory `project_precommit_hook_execution_model` 实证）：pre-commit hook 在 worktree 内跑 `uv run python -m pytest -m e2e_smoke`——**收集 worktree 全部测试文件（`-m` 过滤在收集后，收集期 import 所有 test module + conftest），但 import 的 `octoagent.*` 源码经共享 venv editable 指向主仓 master src**。本 Feature 的新模块 `octoagent.skills.testing` 在合入 master 前对 hook 不存在 → 任何 module 级 import 它的被收集文件都会炸 hook。pytest 源码实证：conftest import 异常一律包成 `ConftestImportFailure`（usage error），**conftest 内 `pytest.importorskip` 不可靠**。

防御三件套：
1. **新测试文件**凡 module 级需要 `octoagent.skills.testing` 的，顶部 `pytest.importorskip("octoagent.skills.testing")`——pre-merge 窗口 hook 收集时优雅 SKIP，合入后恒可 import（guard 变永久 no-op，一行成本）。
2. **conftest re-export 翻转放最后（flip-at-the-end）**：Phase B 先落包模块（conftest 不动——窗口内包模块与 conftest 类短暂共存两份实现，每 commit hook 保持绿）；文档 Phase 尾部单独 commit 翻转 conftest 为 re-export + 删 inline 类。`test_runner.py:93` 有 module 级 `class CaptureFeedbackClient(QueueModelClient)`，guarded-fallback（except 分支留 inline 副本）会在合入后变成永久死代码（违背 no-dead-code），故弃用；翻转 commit 起本 worktree 的 commit 需 `SKIP_E2E=1`，以 **PYTHONPATH 锁定 worktree 的 `pytest -m e2e_smoke` 8/8 PASS** 作为补偿 gate（该锁定跑法本就是 worktree 验证的语义正确形态——hook 的 master-src 混跑对 worktree 改动是弱信号）。合入 master 后 hook 恒绿、零残留。
3. **keystone 测试暂不标 `e2e_smoke`**：新 marker `e2e_scripted`（pyproject markers 一行登记——与 F137 的 pyproject 改动是显式合并交点，报告列出），叠加既有正交 marker `e2e_live`。是否升 smoke 由合入后主 session 决定（F141 三模式 lane 归入 pr lane）。

---

## 4. Keystone 验收锚（L3 e2e 样例设计）

**测试**：`apps/gateway/tests/e2e_live/test_e2e_scripted_decision_loop.py`（新 marker `e2e_scripted` + 正交 `e2e_live`，暂不入 e2e_smoke——§3.5）

```python
async def test_scripted_adapter_drives_real_decision_loop(scripted_harness):
    # 1. harness 带脚本脑 bootstrap 全 11 段（真 store / 真 tool_broker / 真 SnapshotStore，
    #    credential_store=CredentialStore(空 tmp 文件)（load 返回空 store，无需真 OAuth → CI 可跑！），
    #    model_client=ScriptedModelClient([
    #      SkillOutputEnvelope(tool_calls=[ToolCallSpec("user_profile.update", {偏好写入})]),
    #      SkillOutputEnvelope(content="已记录", complete=True),
    #    ]))
    # 1b. 防御断言装置：app.state.provider_router.call 替换为 raise AssertionError 的 bomb
    #     （测试侧 patch，证明全程零真 provider 调用；F137 硬闸落地后是第二重兜底）
    # 2. 驱动真决策环（keystone MVP 走 direct 入口；selected_tools 经
    #    extract_mounted_tool_names 的 metadata["selected_tools_json"] 通道；
    #    permission_preset=full 让 IRREVERSIBLE 的 user_profile.update 直放——
    #    keystone 验证决策环不验证 ApprovalGate，同 F087 smoke 域 #1 先例）：
    result = await app.state.llm_service.call(
        "记住我喜欢简洁回复", task_id=tid, trace_id=tid,
        metadata={"selected_tools_json": ["user_profile.update"],
                  "permission_preset": "full"})
    # 3. 断言（≥2 独立断言点，spec 房规）：
    assert scripted.calls == 2                              # 决策环真跑了 2 轮（脚本脑被消费到 complete）
    assert TOOL_CALL_STARTED(user_profile.update) in events # 决策→broker 派发真发生（前半段！）
    assert "简洁" in USER_md_content                          # 回写真落盘（后半段）
    assert MEMORY_ENTRY_ADDED in events                     # 事件真产
    assert result.content == "已记录"                        # 脚本脑输出贯穿到 ModelCallResult（没落 Echo fallback）
    # 零真 provider HTTP：由 1b bomb + 空凭证双重构造性保证
```

**为什么这是 keystone**：它与 `test_e2e_basic_tool_context.py` 的**唯一差别**——后者从 `tool_broker.execute()` 切进（跳过决策），本测试让**脚本化 LLM 决定**调 `user_profile.update`，**完整跑决策环前半段**。这就是 L3 此前零覆盖的那一跳。

**附加价值（CI 可跑）**：脚本化路径**不需要真 OAuth**（不打 provider）→ 摆脱 e2e_smoke 的 `real_codex_credential_store` 宿主依赖 → **可进干净 CI**（补 M9 "CI 断链、L2/L3 只能宿主机跑" 的洞）。这是 keystone 之外的战略红利。

---

## 5. AC（验收标准）

| AC | 断言 | 绑定测试（SDD AC↔test 显式绑定）|
|----|------|--------------------------------|
| AC-1 | `OctoHarness(model_client=X)` 时 SkillRunner 用 X（不用 ProviderModelClient），且**与 llm_mode 解耦**（echo 模式也建 SkillRunner）| `apps/gateway/tests/test_octo_harness_model_client_di.py` |
| AC-2 | `model_client=None AND clock=None` 时行为与 master 等价（§3.2 None 等价语义：非 echo 建 ProviderModelClient-SkillRunner / echo 跳过 / `app.state.clock` 为 inert 默认）| `apps/gateway/tests/test_octo_harness_di_none_equivalence.py` |
| AC-3【keystone】| 脚本化 adapter 驱动真 SkillRunner → 真 tool_broker.execute → 真回写（USER.md + MEMORY_ENTRY_ADDED），零真 provider HTTP | `apps/gateway/tests/e2e_live/test_e2e_scripted_decision_loop.py` |
| AC-4 | `ScriptedModelClient` 多步链：第 1 轮 tool_call A、第 2 轮 tool_call B、第 3 轮 complete，决策环按序消费 | `packages/skills/tests/test_scripted_model_multistep.py` |
| AC-5 | `QueueModelClient` 上提后 `test_runner.py` 等 8 个既有消费文件零改动通过（re-export 兼容）| `packages/skills/tests/test_runner.py` 等（既有）|
| AC-6 | clock DI：注入固定时钟后 watchdog 时间判断确定性可测（F103d offset-naive 类 bug 在 L4 可抓）| `apps/gateway/tests/test_watchdog_clock_di.py` |
| AC-7 | ~~SchemaTestAdapter~~ **Phase 2 deferred（拍板②，§2.2）**，本次不实施不验收 | —— |
| AC-8 | keystone L3 测试**不依赖宿主 OAuth**（空 tmp CredentialStore 即可跑）→ CI-runnable | `test_e2e_scripted_decision_loop.py`（fake cred fixture，不依赖 `real_codex_credential_store`）|
| AC-9 | 0 regression vs 本 worktree rebase 后 baseline（4919 passed / 14 skipped / 1 xfailed / 1 xpassed），e2e_smoke 8/8 | 全量回归 |

---

## 6. 次要决策（已定，随收窄一并锁定）

- **marker = 新 `e2e_scripted`**（+ 正交 `e2e_live`）：keystone 测试无 OAuth 依赖、可进 CI，语义上是"确定性决策环"独立层；纳入 e2e_smoke 会继承其宿主 OAuth SKIP 逻辑 + 触发 §3.5 hook 陷阱。F141 三模式 lane 时归入 pr lane（每次都跑）；是否升 smoke 合入后主 session 决定。
- **clock 接 watchdog**：接（单一 consumer 子系统 6 调用点，坐实 F103d bug 价值，§3.3）。
- **keystone 走 direct 入口**（`llm_service.call`）；/api/message 全链路（含 TaskService 编排 + selected_tools 上游填充）作后续广度样例，不在本 Feature。

---

## 7. 宪法自查（Constitution）

| # | 条款 | F138 边界 |
|---|------|----------|
| #9 | 禁硬编码替代 LLM 决策 | **脚本化只在测试层**：`testing` 子模块 + harness DI 默认 None + 生产 `main.py:425` 只传 `project_root` 不传 override → 生产决策环恒用真 `ProviderModelClient`。构造性不可达，非约定。override 分支只由测试构造入口触达，不新增任何生产可配置开关（无 env / 无 yaml 字段可开启）。 |
| #6 | Degrade Gracefully | DI 全 None 生产等价；脚本件缺失不影响生产。 |
| #3 | Tools are Contracts | 脚本件产的 `ToolCallSpec` 走真 `tool_broker.execute` 同一 schema/policy 校验链路，不绕过契约（SchemaTestAdapter deferred，届时复用 `parameters_json_schema` 单一事实源）。 |
| #2 | Everything is an Event | 脚本化决策环产的事件（MODEL_STARTED/COMPLETED、TOOL_*、MEMORY_ENTRY_ADDED）与真 LLM 路径同链路，L3 可断言事件链。 |

**双评审要求**（重大架构变更：touches harness 核心 + 新 provider/skills 测试件）：Codex + Opus 双评审 0 HIGH 后再合。

---

## 8. 规模复核

原估 L，SchemaTestAdapter 裁到 Phase 2 后首版收敛 **M-L**：
- model_client DI + 拦截硬连 + None 等价对账：**M**（harness bootstrap 核心，须严守 None 行为等价）
- QueueModelClient 上提 + re-export（flip-at-the-end）：**S**（已存在）
- clock DI seam + watchdog consumer（6 调用点）：**S-M**
- keystone L3 e2e + 多步样例 + fake-cred fixture：**M**
- ~~SchemaTestAdapter + 私有 API 签名锁~~：**Phase 2 deferred**
- 文档漂移修（e2e-testing.md / testing-strategy.md）：**S**
- 双评审 + None 等价对账的 rigor 开销：叠加
