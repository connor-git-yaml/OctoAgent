# 修复规划：077-fix-mcp-tool-permission

**Branch**: `claude/affectionate-hellman-d84725`
**Date**: 2026-04-18
**Mode**: fix
**Spec**: `.specify/features/077-fix-mcp-tool-permission/fix-report.md`

---

## Summary

本次修复两个级联 bug，共同根因是"同一判断逻辑分散在两处、各自独立维护"：

1. **Runner 白名单未放行 MCP 工具**：`runner.py:_execute_tool_calls` 的执行前白名单校验没有同步 `litellm_client.py:_load_permitted_tools` 的 `is_mcp` 特判，导致 LLM 看得见 `mcp.*` 工具但 Runner 拒绝执行。

2. **Responses API 孤立 call_id**：`litellm_client.py:_history_to_responses_input` 的新 Chat Completions 格式分支缺少"预扫 known_call_ids → 过滤孤立 function_call_output"的防御逻辑（该防御在旧 type-based 分支存在，新格式迁移时未携带过来）。

修复方案：提炼公共函数消除重复、在 runner 执行层补对称防御、在 Responses 输入构建层补 call_id 配对校验。

---

## Technical Context

**语言/版本**: Python 3.12+  
**包**: `octoagent/packages/skills`（单包内修复，无跨包变更）  
**测试框架**: pytest  
**涉及文件总数**: 3 个源文件 + 2 个测试文件  
**变更性质**: 纯防御性修复 + 一致性对齐，无新增公共 API 或 schema 变更

---

## Codebase Reality Check

| 文件 | LOC | 公开方法/函数 | 已知 debt |
|------|-----|--------------|-----------|
| `skills/models.py` | 455 | 2 个模块级函数（`extract_mounted_tool_names`、`resolve_effective_tool_allowlist`） + 9 个 Pydantic 模型 | 无 TODO/FIXME；`LoopGuardPolicy` 标有 `@deprecated` 但保留兼容 |
| `skills/runner.py` | 929 | 1 个公开类 `SkillRunner`，主要方法：`run`、`_execute_tool_calls`、`_execute_single_tool` | 无 TODO/FIXME；`run()` 方法 ~290 行（属合理复杂度，无需拆分）|
| `skills/litellm_client.py` | 904 | 1 个公开类 `LiteLLMSkillClient`，主要方法：`generate`、`_call_proxy_responses`、`_history_to_responses_input`、`_get_tool_schemas` | 无 TODO/FIXME；`_history_to_responses_input` 新旧格式分支共存，本次修复正是处理该问题 |

**前置清理判定**: 三个文件均无超标 debt（TODO=0，无 30 行以上重复块），不触发前置 cleanup task。

---

## Impact Assessment

| 维度 | 详情 |
|------|------|
| 直接修改文件 | 3 个源文件（models.py、runner.py、litellm_client.py） |
| 测试文件 | 2 个（test_runner.py、test_litellm_client.py，新增测试用例） |
| 跨包影响 | 无（全部在 `skills` 包内） |
| 数据迁移 | 无 |
| API/契约变更 | 无（新增的 `is_runtime_exempt_tool` 是包内私用函数，不进入公共 API） |
| 调用方需更新 | 无（`_execute_tool_calls` 和 `_history_to_responses_input` 均为内部方法） |

**风险等级: LOW**

影响文件 < 10，无跨包影响，无数据迁移，无公共接口变更，属于纯防御性补丁。

---

## Constitution Check

| 原则 | 适用性 | 评估 | 说明 |
|------|--------|------|------|
| 原则 3：Tools are Contracts | 直接相关 | PASS | 修复确保工具 schema 放行层与执行层行为一致，消除双重独立判断 |
| 原则 10：Policy-Driven Access | 直接相关 | PASS | `is_runtime_exempt_tool` 将 MCP 豁免决策收敛到单一函数，符合"权限判断收敛到单一入口"要求 |
| 原则 8：Observability | 间接相关 | PASS | 不修改任何事件发射逻辑，Responses API 400 错误仍会正常记录 |
| 原则 13A：优先上下文而非硬策略 | 间接相关 | PASS | `is_runtime_exempt_tool` 是必要的基础设施判断（权限/安全硬边界），不属于"过度防御式代码特判" |
| 其余原则 | 不适用 | N/A | 无副作用操作、无状态持久化、无 A2A 协议变更 |

**结论**: 无 VIOLATION，Constitution Check 通过。

---

## 关键决策：`is_runtime_exempt_tool` 放在哪里

**决策**: 放入 `models.py`，不新建 `tool_runtime_policy.py`。

**理由**：
- `models.py` 已有 `resolve_effective_tool_allowlist` 等工具权限相关函数，职责一致（"工具白名单 / 豁免策略的数据逻辑"）
- `runner.py` 已从 `models.py` 导入 `resolve_effective_tool_allowlist`，加入 `is_runtime_exempt_tool` 不引入新依赖
- `litellm_client.py` 已从 `models.py` 导入 `resolve_effective_tool_allowlist`，同理
- 两处调用都需要它，放在共同的 `models.py` 天然消除循环导入风险
- 新建 `tool_runtime_policy.py` 只有 1 个函数，是过度拆分；models.py 455 行添加 ~8 行后仍为合理体量

**被拒绝的替代方案**: `tool_runtime_policy.py` — 文件粒度过细，1 个函数不值得单独建模块。

---

## 变更清单（单阶段完成）

本次修复不分阶段，所有变更在同一 batch 完成，因为三处源码修改高度相关（bug 2 在 bug 1 触发路径上），分开合入会产生中间状态：MCP 工具能执行但孤立 call_id 未防御。

### 变更 1：`models.py` — 新增公共函数

**位置**: 在 `resolve_effective_tool_allowlist` 函数之后（第 455 行后追加）

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

**导出**: 在 `runner.py` 的 import 块中加入 `is_runtime_exempt_tool`。

---

### 变更 2：`runner.py` — `_execute_tool_calls` 白名单校验加 MCP 放行

**位置**: 第 410-414 行，白名单 for 循环内部

**修改前**:
```python
for call in tool_calls:
    if allowed_tool_names and call.tool_name not in allowed_tool_names:
        raise SkillToolExecutionError(
            f"工具 '{call.tool_name}' 不在当前 skill 可用工具集合中"
        )
```

**修改后**:
```python
for call in tool_calls:
    if allowed_tool_names and call.tool_name not in allowed_tool_names:
        # MCP 动态工具豁免：查询 tool_group 后委托公共函数判断
        tool_meta = await self._tool_broker.get_tool_meta(call.tool_name)
        tool_group = tool_meta.tool_group if tool_meta else ""
        if is_runtime_exempt_tool(call.tool_name, tool_group):
            continue
        raise SkillToolExecutionError(
            f"工具 '{call.tool_name}' 不在当前 skill 可用工具集合中"
        )
```

**注意**: `get_tool_meta` 仅在工具不在白名单时才调用，不影响正常路径性能。

---

### 变更 3：`litellm_client.py` — 两处修改

**3a. `_get_tool_schemas` 内联 `is_mcp` 替换为公共函数调用**

**位置**: 第 151-159 行

**修改前**（内联逻辑）:
```python
is_mcp = getattr(tool_meta, "tool_group", "") == "mcp" and tool_meta.name.startswith("mcp.") and "." in tool_meta.name[4:]
```

**修改后**（调用公共函数）:
```python
from .models import is_runtime_exempt_tool  # 加入文件顶部 import 块

# 循环内:
is_mcp = is_runtime_exempt_tool(tool_meta.name, getattr(tool_meta, "tool_group", ""))
```

**3b. `_history_to_responses_input` 加 call_id 配对预扫**

**位置**: 函数开头，在 `items: list[dict[str, Any]] = []` 之后

**新增预扫逻辑**:
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
    # 兼容旧 type-based 格式（虽然不应再出现，但防御保留）
    elif str(message.get("type", "")).strip() == "function_call":
        cid = str(message.get("call_id", "")).strip()
        if cid:
            known_call_ids.add(cid)
```

**修改 `role == "tool"` 分支，加入 call_id 过滤**:

**修改前**:
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

**修改后**:
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

---

### 变更 4：`test_runner.py` — 新增白名单 + MCP 放行正向测试

新增测试用例，位于现有白名单测试区块之后：

```python
# 测试场景：MCP 工具不在 skill tools_allowed 但应被放行
async def test_mcp_tool_not_blocked_by_allowlist():
    """mcp.servers.list 即使不在 tools_allowed 也不应被 runner 拒绝。"""
    ...

# 测试场景：非 MCP 工具不在白名单时应被拒绝（回归）
async def test_non_mcp_tool_blocked_by_allowlist():
    """确认修复没有破坏原有白名单拒绝逻辑。"""
    ...
```

---

### 变更 5：`test_litellm_client.py` — 新增孤立 function_call_output 过滤测试

新增测试用例，覆盖 `_history_to_responses_input` 新行为：

```python
# 测试场景：正常配对的 tool message 应保留
def test_history_to_responses_input_paired_tool_included():
    ...

# 测试场景：孤立的 tool message（无对应 assistant.tool_calls）应被过滤
def test_history_to_responses_input_orphan_tool_filtered():
    """孤立 function_call_output 不进入 Responses API input，避免 400 错误。"""
    ...

# 测试场景：旧 type-based 格式的孤立消息也被过滤（兼容层）
def test_history_to_responses_input_legacy_orphan_filtered():
    ...
```

---

## 回归风险评估

| 风险场景 | 影响 | 缓解措施 |
|---------|------|---------|
| `get_tool_meta` 在白名单拒绝路径返回 `None` | `tool_group` 为空字符串，`is_runtime_exempt_tool` 返回 `False`，原有拒绝逻辑保持不变 | 代码分支本身安全；测试覆盖 `meta=None` 场景 |
| `known_call_ids` 预扫漏掉某些有效 call_id | 正常的 assistant.tool_calls 消息会被预扫收录；只有没有对应 assistant 消息的孤立 tool 消息会被过滤，这正是期望行为 | 新增正常配对场景的回归测试 |
| `is_runtime_exempt_tool` 导入循环 | `runner.py` 和 `litellm_client.py` 都已从 `models.py` 导入，无新依赖关系 | 现有导入路径验证 |
| MCP 工具名格式变化（如不带点） | `is_runtime_exempt_tool` 要求 `tool_name.startswith("mcp.")` 且 `"." in tool_name[4:]`，与现有 schema 层判断完全一致 | 逻辑复用同一函数，不会产生新的格式漂移 |

---

## 验证方案

### 单元测试验证

```bash
cd /Users/connorlu/Desktop/.workspace2.nosync/OctoAgent/.claude/worktrees/affectionate-hellman-d84725
uv run pytest octoagent/packages/skills/tests/test_runner.py -v -k "mcp or allowlist"
uv run pytest octoagent/packages/skills/tests/test_litellm_client.py -v -k "orphan or tool_filtered or responses_input"
```

### 回归测试

```bash
uv run pytest octoagent/packages/skills/tests/ -v --tb=short
```

### 手动验证路径

1. 启动系统，在 Web UI 指派 Agent 执行 MCP 安装任务
2. 确认 `mcp.servers.list` 不再出现"不在当前 skill 可用工具集合中"错误
3. 确认 Responses API 不再返回 400 "No tool call found for function call output"

---

## 文件结构（本次修复涉及）

```text
octoagent/packages/skills/
├── src/octoagent/skills/
│   ├── models.py          # 新增 is_runtime_exempt_tool()
│   ├── runner.py          # _execute_tool_calls 白名单加豁免判断
│   └── litellm_client.py  # _get_tool_schemas 换公共函数；_history_to_responses_input 加预扫
└── tests/
    ├── test_runner.py         # 新增 MCP 放行正向测试 + 非 MCP 拒绝回归
    └── test_litellm_client.py # 新增孤立 function_call_output 过滤测试
```

---

## 不生成的制品

fix 模式下，以下制品不生成（不涉及新数据模型或对外契约变更）：
- `data-model.md`
- `contracts/`
- `quickstart.md`
- `research.md`
