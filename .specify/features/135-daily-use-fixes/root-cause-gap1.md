# Gap-1 根因结论：behavior.write_file 在聊天会话"看似没暴露"

> 结论先行：**是真 bug，不是"设计如此"**，但根因**不在 entrypoint 过滤**（那层是死代码），
> 而在**工具分层（Core vs Deferred）**——`behavior.write_file` 是 Deferred 工具，
> 主 Agent 必须先走 `tool_search` 两跳链路才能激活它，导致核心引导闭环脆弱。

## 经验复现（非纯推理）

复现脚本 `scratchpad/repro_gap1.py` 启动**真实 OctoHarness**（生产 wiring），
按 `orchestrator._resolve_single_loop_tool_selection` 同款方式为"主 Agent（web 来源）"
调用 `resolve_profile_first_tools`，输入 query="帮我完成 USER.md 初始化"。实测输出：

```
behavior.write_file in pack.tools?  True
  entrypoints= ['agent_runtime']       # 只声明 agent_runtime，不含 web
  tool_group= behavior
  tool_profile= standard
  availability= available
behavior.write_file is_core?  False    # ← 关键：不在 CoreToolSet.default()

main agent profile:
  profile_id= agent-profile-system-default
  kind= main
  default_tool_groups= []              # ← 主 Agent profile 自身没配 groups

resolve_profile_first_tools 结果（主 Agent web）:
  mounted (完整 schema 给 LLM)= [graph_pipeline, filesystem.list_dir,
     filesystem.read_text, filesystem.write_text, terminal.exec, web.fetch,
     web.search, memory.recall, skills, delegate_task]   # ← 10 个，无 behavior.write_file
  behavior.write_file mounted?  False
  behavior.write_file deferred? True    # ← 它在 deferred 清单里（注入 system prompt 文本）
  behavior.write_file blocked?  False

tool_search("修改 USER.md 行为文件"):
  hits[0]= behavior.write_file          # ← tool_search 能召回它（排第 1）
  discoverable via tool_search?  True
```

## 逐层定位（file:line 证据）

### 层 1（被误导的假线索）：entrypoint 过滤是**死代码**
- `behavior.write_file` 声明 `entrypoints=frozenset({"agent_runtime"})`：
  `apps/gateway/src/octoagent/gateway/services/builtin_tools/misc_tools.py:46`。
- 合法 entrypoint `{web, agent_runtime, telegram}`：`harness/tool_registry.py:30`。
- `resolve_for_entrypoint`（`harness/toolset_resolver.py:130`）**在生产代码零调用者**
  （全仓 grep 仅定义处命中）。`toolsets.yaml` 的两层过滤同样无生产消费者。
- ∴ `entrypoints=["agent_runtime"]` **并没有**把工具挡在 web 之外。假设"web 入口过滤掉
  agent_runtime-only 工具"是**错的**——真实链路根本不看 entrypoint。

### 层 2（真正的选择引擎）：`resolve_profile_first_tools`
`apps/gateway/src/octoagent/gateway/services/capability_pack.py:524-762`
- 主 Agent（web）确实走这里：`orchestrator.py:1028`（`_resolve_single_loop_tool_selection`
  → `capability_pack.resolve_profile_first_tools`，worker_type="general"，
  requested_profile_id=主 Agent profile）。
- `desired_tools` 组成（:543-553）= `binding.selected_tools` +
  `_profile_first_candidate_tool_names()` + **`tool_group in binding.default_tool_groups`** 的工具。
- 过滤条件只有三个：工具存在 / `tool_profile` 允许（:582）/ availability（:600）。
  **entrypoint 完全不进过滤**（:596/:614/:638/:696 仅把 entrypoints 塞进 metadata 供 UI/日志）。

### 层 3（决定性）：Core vs Deferred 分层
- `binding.default_tool_groups`：主 Agent profile（kind=main，`default_tool_groups=[]`）
  经 `resolve_worker_binding` 非 worker 分支回退到 builtin general 的 groups
  （`capability_pack.py:459` → `list(builtin_profile.default_tool_groups)`）。
- builtin general 的 `_UNIFIED_TOOL_GROUPS` **含 `"behavior"`**（`capability_pack.py:919`）。
  ∴ `behavior.write_file`（tool_group="behavior"）**进了 desired_tools**、通过全部过滤。
- 但 `core_set.is_core("behavior.write_file")` = **False**（`CoreToolSet.default()`,
  `packages/tooling/src/octoagent/tooling/models.py:396-410` 清单里没有它）→
  落进 `else` 分支变成 **deferred**（`capability_pack.py:655-663`）。
- deferred 工具只把 {name, 一行描述} 注入 system prompt（`llm_service.py:392-403`），
  **不给完整 schema**，LLM 想调用必须先 `tool_search` 激活（两跳）。

## 为什么用户看到"工具入口没暴露给我"

主 Agent 的**直接可调用工具集**（完整 schema 的 10 个）里没有 `behavior.write_file`；
它只在 system prompt 的 deferred 清单里以文本形式出现（所以 Agent "知道该用它"）。
Agent 要用它必须先 `tool_search` 命中 → 激活 → 再调用。这条两跳链路对弱 model /
单轮场景不可靠——Agent 直接回复"入口没暴露给我"，核心引导闭环（首次见面填 USER.md）
在生产走不通。

## 这不是"设计如此需产品决策"，是可直接修的 bug

判据：`CoreToolSet.default()`（`models.py:406-409`）里 `graph_pipeline` / `delegate_task`
的注释**明确记录**了同一类问题——"不进 Core 会被压成 deferred → 强制走 tool_search 两跳链路
→ 来不及 → SKIP"，当时的解法就是**把它们提进 Core**。`behavior.write_file` 是完全同构的
情况（首次引导闭环高频、语义关键），提进 Core 是**已确立的先例**，非新权限模型。

## 修复方案（不绕过治理）

把 `behavior.write_file` 加入 `CoreToolSet.default()`（`packages/tooling/.../models.py`），
让主 Agent 直接拿到完整 schema、一跳可调用。

**治理不被绕过**（Constitution #4/#7/#10 守住）：
- `behavior_write_file` handler 自身强制 Two-Phase：`review_mode == REVIEW_REQUIRED and
  not confirmed → 返回 proposal（status=skipped）`（`misc_tools.py:258`）。
- USER.md 的 review_mode = `REVIEW_REQUIRED`
  （`packages/core/.../behavior_workspace/template.py:50-58`）。
- ∴ 提进 Core 只改变"可见性/可直接调用"，**不改变**"写 USER.md 必须先 proposal→用户确认→
  confirmed=true 再落盘"的治理链。Core/Deferred 是**发现层**，治理在**执行层**，两者正交。

## 边界确认

- 不动 entrypoint 死代码（超出 F135 范围；可在 living-docs 记为已知冗余）。
- 不裸暴露绕过审批：提 Core 后 USER.md 写仍走 REVIEW_REQUIRED proposal。
- 不无脑塞进所有 surface：改的是 Core 工具清单（对所有 surface 生效，但治理执行层不变）。
