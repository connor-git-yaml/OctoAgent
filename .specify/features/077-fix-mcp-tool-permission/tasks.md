# 任务清单：077-fix-mcp-tool-permission

**Branch**: `claude/affectionate-hellman-d84725`
**Date**: 2026-04-18
**Mode**: fix
**风险等级**: LOW
**总任务数**: 6
**依赖结构**: task-001 → task-002, task-003a；task-003b 独立；task-004, task-005 均依赖 task-001 + task-002/003

---

## 依赖关系总览

```
task-001（models.py 新增公共函数）
    ├──▶ task-002（runner.py 调用公共函数）
    │        └──▶ task-004（test_runner.py 新增测试）
    └──▶ task-003a（litellm_client.py 替换内联 is_mcp）
             └──▶ task-005（test_litellm_client.py 新增测试）
task-003b（litellm_client.py 加 call_id 预扫防御，与 003a 同文件，顺序执行）
    └──▶ task-005

task-006（集成验证）依赖所有前序任务完成
```

**并行说明**：
- task-002 和 task-003a + task-003b 可在 task-001 完成后并行（不同文件）
- task-004 和 task-005 可在各自依赖就绪后并行

---

## task-001：models.py — 新增 `is_runtime_exempt_tool` 公共函数

**文件**: `/Users/connorlu/Desktop/.workspace2.nosync/OctoAgent/.claude/worktrees/affectionate-hellman-d84725/octoagent/packages/skills/src/octoagent/skills/models.py`

**操作**: 在文件末尾（`resolve_effective_tool_allowlist` 函数之后，约第 455 行后）追加新函数。

**内容**：
```python
def is_runtime_exempt_tool(tool_name: str, tool_group: str) -> bool:
    """判断工具是否属于 runtime 豁免类别（不受静态白名单限制）。

    当前豁免类别：MCP 动态工具（tool_group == "mcp"）。
    MCP 工具在注册时不可预知具体名称，由 LiteLLM schema 层动态放行；
    执行层通过本函数保持与 schema 层一致的豁免判断。
    """
    return (
        tool_group == "mcp"
        and tool_name.startswith("mcp.")
        and "." in tool_name[4:]
    )
```

**验证**：
```bash
grep -n "is_runtime_exempt_tool" /Users/connorlu/Desktop/.workspace2.nosync/OctoAgent/.claude/worktrees/affectionate-hellman-d84725/octoagent/packages/skills/src/octoagent/skills/models.py
```

**前置依赖**: 无

---

## task-002：runner.py — `_execute_tool_calls` 白名单校验加 MCP 豁免

**文件**: `/Users/connorlu/Desktop/.workspace2.nosync/OctoAgent/.claude/worktrees/affectionate-hellman-d84725/octoagent/packages/skills/src/octoagent/skills/runner.py`

**操作**：
1. 在文件顶部 import 块中，从 `.models` 补充导入 `is_runtime_exempt_tool`
2. 定位 `_execute_tool_calls` 方法，约第 410-414 行，修改白名单拒绝分支：

**修改前**（约 L410-414）：
```python
for call in tool_calls:
    if allowed_tool_names and call.tool_name not in allowed_tool_names:
        raise SkillToolExecutionError(
            f"工具 '{call.tool_name}' 不在当前 skill 可用工具集合中。请稍后重试。"
        )
```

**修改后**：
```python
for call in tool_calls:
    if allowed_tool_names and call.tool_name not in allowed_tool_names:
        # MCP 动态工具豁免：仅在不命中白名单时才查询 tool_group
        tool_meta = await self._tool_broker.get_tool_meta(call.tool_name)
        tool_group = tool_meta.tool_group if tool_meta else ""
        if is_runtime_exempt_tool(call.tool_name, tool_group):
            continue
        raise SkillToolExecutionError(
            f"工具 '{call.tool_name}' 不在当前 skill 可用工具集合中。请稍后重试。"
        )
```

**验证**：
```bash
grep -n "is_runtime_exempt_tool" /Users/connorlu/Desktop/.workspace2.nosync/OctoAgent/.claude/worktrees/affectionate-hellman-d84725/octoagent/packages/skills/src/octoagent/skills/runner.py
```

**前置依赖**: task-001（`is_runtime_exempt_tool` 必须已定义）

---

## task-003a：litellm_client.py — `_get_tool_schemas` 内联 `is_mcp` 替换为公共函数

**文件**: `/Users/connorlu/Desktop/.workspace2.nosync/OctoAgent/.claire/worktrees/affectionate-hellman-d84725/octoagent/packages/skills/src/octoagent/skills/litellm_client.py`

**实际文件路径**: `/Users/connorlu/Desktop/.workspace2.nosync/OctoAgent/.claude/worktrees/affectionate-hellman-d84725/octoagent/packages/skills/src/octoagent/skills/litellm_client.py`

**操作**：
1. 在文件顶部 import 块中，从 `.models` 补充导入 `is_runtime_exempt_tool`（若该文件已导入 `resolve_effective_tool_allowlist`，则在同一 import 行追加）
2. 定位 `_get_tool_schemas`（或 `_load_permitted_tools`）内约第 151-159 行的内联 `is_mcp` 表达式：

**修改前**（约 L151-159 内联逻辑）：
```python
is_mcp = (
    getattr(tool_meta, "tool_group", "") == "mcp"
    and tool_meta.name.startswith("mcp.")
    and "." in tool_meta.name[4:]
)
```

**修改后**：
```python
is_mcp = is_runtime_exempt_tool(tool_meta.name, getattr(tool_meta, "tool_group", ""))
```

**验证**：
```bash
grep -n "is_runtime_exempt_tool\|is_mcp" /Users/connorlu/Desktop/.workspace2.nosync/OctoAgent/.claude/worktrees/affectionate-hellman-d84725/octoagent/packages/skills/src/octoagent/skills/litellm_client.py
```

**前置依赖**: task-001

---

## task-003b：litellm_client.py — `_history_to_responses_input` 加 call_id 配对预扫防御

**文件**: `/Users/connorlu/Desktop/.workspace2.nosync/OctoAgent/.claude/worktrees/affectionate-hellman-d84725/octoagent/packages/skills/src/octoagent/skills/litellm_client.py`

**注意**: 与 task-003a 同文件，应在 task-003a 完成后顺序执行（或一次性读取文件后同批修改）。

**操作**：
1. 定位 `_history_to_responses_input` 方法（约第 310-386 行），在 `items: list[dict[str, Any]] = []` 声明之后插入预扫逻辑：

```python
# 预扫 known_call_ids：收集所有 assistant.tool_calls 中的 call_id
# 防止 function_call_output 孤立（无对应 function_call）导致 Responses API 400
known_call_ids: set[str] = set()
for message in history:
    role = str(message.get("role", "")).strip()
    if role == "assistant":
        for tc in message.get("tool_calls") or []:
            cid = str(tc.get("id", "")).strip()
            if cid:
                known_call_ids.add(cid)
    # 兼容旧 type-based 格式（防御保留）
    elif str(message.get("type", "")).strip() == "function_call":
        cid = str(message.get("call_id", "")).strip()
        if cid:
            known_call_ids.add(cid)
```

2. 定位 `role == "tool"` 分支（约第 337-357 行），修改为：

**修改前**：
```python
if role == "tool":
    call_id = str(message.get("tool_call_id", "")).strip()
    if call_id:
        items.append({
            "type": "function_call_output",
            "call_id": call_id,
            "output": str(message.get("content", "")),
        })
    continue
```

**修改后**：
```python
if role == "tool":
    call_id = str(message.get("tool_call_id", "")).strip()
    if call_id and call_id in known_call_ids:  # 过滤孤立的 function_call_output
        items.append({
            "type": "function_call_output",
            "call_id": call_id,
            "output": str(message.get("content", "")),
        })
    elif call_id and call_id not in known_call_ids:
        log.warning(
            "orphan_tool_message_skipped",
            call_id=call_id,
            known_count=len(known_call_ids),
        )
    continue
```

**验证**：
```bash
grep -n "known_call_ids\|orphan_tool_message_skipped" /Users/connorlu/Desktop/.workspace2.nosync/OctoAgent/.claude/worktrees/affectionate-hellman-d84725/octoagent/packages/skills/src/octoagent/skills/litellm_client.py
```

**前置依赖**: task-001（在同文件内，与 task-003a 顺序完成）

---

## task-004：test_runner.py — 新增 MCP 放行正向测试 + 非 MCP 拒绝回归测试

**文件**: `/Users/connorlu/Desktop/.workspace2.nosync/OctoAgent/.claude/worktrees/affectionate-hellman-d84725/octoagent/packages/skills/tests/test_runner.py`

**操作**: 在现有 `test_runner_disallowed_tool`（约第 136 行）测试之后，新增以下两个测试用例：

**test_mcp_tool_not_blocked_by_allowlist**：
- 构造一个 `RESTRICT` 模式的 manifest，`tools_allowed` 不包含 `mcp.servers.list`
- mock `tool_broker.get_tool_meta` 对 `mcp.servers.list` 返回 `tool_group="mcp"` 的 meta
- 确认 runner 不抛出 `SkillToolExecutionError`，工具调用正常执行
- 验证 MCP 豁免放行路径

**test_non_mcp_tool_blocked_by_allowlist**：
- 构造一个 `RESTRICT` 模式的 manifest，`tools_allowed` 不包含 `danger.exec`
- mock `tool_broker.get_tool_meta` 对 `danger.exec` 返回 `tool_group="system"` 的 meta
- 确认 runner 抛出 `SkillToolExecutionError`（回归：非 MCP 工具不得被豁免）

**验证**：
```bash
cd /Users/connorlu/Desktop/.workspace2.nosync/OctoAgent/.claude/worktrees/affectionate-hellman-d84725 && uv run pytest octoagent/packages/skills/tests/test_runner.py -v -k "mcp or allowlist" 2>&1 | tail -20
```

**前置依赖**: task-001 + task-002

---

## task-005：test_litellm_client.py — 新增孤立 function_call_output 过滤测试

**文件**: `/Users/connorlu/Desktop/.workspace2.nosync/OctoAgent/.claude/worktrees/affectionate-hellman-d84725/octoagent/packages/skills/tests/test_litellm_client.py`

**操作**: 在现有测试末尾新增三个测试用例，直接测试 `_history_to_responses_input` 方法：

**test_history_to_responses_input_paired_tool_included**：
- 构造 history：`[assistant with tool_calls id=call_abc, tool with tool_call_id=call_abc]`
- 调用 `_history_to_responses_input`（或通过 `generate` 路径触发）
- 断言输出 items 包含 `{"type": "function_call_output", "call_id": "call_abc", ...}`

**test_history_to_responses_input_orphan_tool_filtered**：
- 构造 history：`[tool with tool_call_id=call_orphan]`（无对应 assistant.tool_calls）
- 断言输出 items 中**不包含**任何 `type == "function_call_output"` 的 item

**test_history_to_responses_input_legacy_orphan_filtered**：
- 构造旧格式 history：`[{type: "function_call_output", call_id: "call_legacy_orphan", ...}]`（无对应 `type=function_call` 消息）
- 断言孤立消息被过滤

**验证**：
```bash
cd /Users/connorlu/Desktop/.workspace2.nosync/OctoAgent/.claude/worktrees/affectionate-hellman-d84725 && uv run pytest octoagent/packages/skills/tests/test_litellm_client.py -v -k "orphan or paired or responses_input" 2>&1 | tail -20
```

**前置依赖**: task-001 + task-003a + task-003b

---

## task-006：集成验证 — 跑全量相关测试

**操作**: 运行以下验证命令，确认所有新增测试通过，且无回归。

**步骤 1 — 新增测试专跑**：
```bash
cd /Users/connorlu/Desktop/.workspace2.nosync/OctoAgent/.claude/worktrees/affectionate-hellman-d84725
uv run pytest octoagent/packages/skills/tests/test_runner.py -v -k "mcp or allowlist" --tb=short
uv run pytest octoagent/packages/skills/tests/test_litellm_client.py -v -k "orphan or paired or responses_input" --tb=short
```

**步骤 2 — skills 包全量回归**：
```bash
uv run pytest octoagent/packages/skills/tests/ -v --tb=short 2>&1 | tail -40
```

**步骤 3 — 人工验证 `is_runtime_exempt_tool` 导入一致性**：
```bash
grep -rn "is_runtime_exempt_tool" /Users/connorlu/Desktop/.workspace2.nosync/OctoAgent/.claude/worktrees/affectionate-hellman-d84725/octoagent/packages/skills/
# 应出现 3 处：models.py（定义）、runner.py（调用）、litellm_client.py（调用）
```

**步骤 4 — 确认无孤立 inline is_mcp 表达式残留**：
```bash
grep -n "tool_group.*==.*['\"]mcp['\"]" /Users/connorlu/Desktop/.workspace2.nosync/OctoAgent/.claude/worktrees/affectionate-hellman-d84725/octoagent/packages/skills/src/octoagent/skills/litellm_client.py
# 应无结果（已全部替换为 is_runtime_exempt_tool 调用）
```

**验收标准**：
- [ ] `test_mcp_tool_not_blocked_by_allowlist` PASS
- [ ] `test_non_mcp_tool_blocked_by_allowlist` PASS
- [ ] `test_history_to_responses_input_paired_tool_included` PASS
- [ ] `test_history_to_responses_input_orphan_tool_filtered` PASS
- [ ] `test_history_to_responses_input_legacy_orphan_filtered` PASS
- [ ] skills 包全量测试无新增失败
- [ ] `is_runtime_exempt_tool` 共 3 处引用（定义 1 + 调用 2）
- [ ] litellm_client.py 中无内联 `tool_group.*==.*"mcp"` 残留

**前置依赖**: task-001 + task-002 + task-003a + task-003b + task-004 + task-005
