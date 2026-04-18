# 问题修复报告：MCP 工具权限校验不一致 + Responses API call_id 配对防御

**Feature**: `077-fix-mcp-tool-permission`
**Branch**: `claude/affectionate-hellman-d84725`
**Created**: 2026-04-18
**Mode**: fix

---

## 问题描述

用户在 Web UI 让 Agent 安装 openrouter-perplexity MCP 时连续失败：

1. `工具 'mcp.servers.list' 不在当前 skill 可用工具集合中。请稍后重试。`
2. `模型调用连续失败: Responses API returned 400: "No tool call found for function call output with call_id call_5b6im4g11XpEpbsxh5NWk7Aj."`

---

## 5-Why 根因追溯（问题 1：权限拒绝）

| 层级 | 问题 | 发现 |
|------|------|------|
| Why 1 | `mcp.servers.list` 为何被拒绝？ | `runner.py:411` 白名单校验：`call.tool_name not in allowed_tool_names` |
| Why 2 | `allowed_tool_names` 为何不含它？ | `resolve_effective_tool_allowlist` 在 `RESTRICT` 模式下只返回 `manifest.tools_allowed` 原样（`models.py:449-450`）；该 skill 的 `tools_allowed` 不含 `mcp.*` |
| Why 3 | 为何 LLM 仍能调用它？ | `litellm_client.py:_load_permitted_tools` 第 152-157 行的 `is_mcp` 特判放行：`tool_group=="mcp"` 且 `name` 匹配 `mcp.X.Y` 时加入 LLM schema |
| Why 4 | 为何两层行为不一致？ | LLM schema 层（构造工具清单给模型看）和 runner 执行层（执行前白名单）**独立维护** `is_mcp` 判断条件，schema 层放行、执行层未同步 |
| Why 5 | 为何会有这种不一致？ **[ROOT CAUSE]** | commit `471a579 fix(skills): Phase 1 三个 Critical Bug 修复` 在 schema 层加 `is_mcp` 特判时，**未同步更新** runner.py 的白名单校验；两套权限逻辑无共同数据源 |

**Root Cause 1**: `is_mcp` 工具放行策略只在 LLM schema 构造层（`litellm_client.py:_load_permitted_tools`）生效，未同步到执行前白名单校验（`runner.py:_execute_tool_calls`）。

**Root Cause Chain 1**:
症状（`mcp.servers.list` 被拒） → runner.py:411 硬过滤 → skill manifest 不含 mcp.* → LLM 却看得见（schema 放行）→ 两层独立判断 → **471a579 只改 schema 层**

---

## 5-Why 根因追溯（问题 2：Responses API 400）

| 层级 | 问题 | 发现 |
|------|------|------|
| Why 1 | Responses API 为何返回 400？ | `input` 里有 `function_call_output`（call_id=call_5b6...），但无对应 `function_call` |
| Why 2 | 为何 `function_call_output` 孤立？ | history 里有 `{role: "tool", tool_call_id: X}`，但对应的 `{role: "assistant", tool_calls: [{id: X}]}` 未进入 input 转换结果 |
| Why 3 | 为何 assistant tool_calls 消失？ | 多种可能：① compactor 裁剪非对称 ② 跨轮对话片段重组丢失 ③ 权限拒绝/超时等异常路径残留 tool message 但 assistant 被删 |
| Why 4 | 为何 `_history_to_responses_input` 没防御孤立？ | 第 337-357 行的**新 Chat Completions 格式分支**直接按 history 顺序产出 items，**没做 call_id 配对预扫** |
| Why 5 | 旧分支有防御，为何新分支没有？ **[ROOT CAUSE]** | commit `3c0a188 fix(skills): 修复 Responses API call_id 不匹配和压缩孤立问题` 的 call_id 配对校验**只覆盖旧 type-based 格式分支**（366-385 行）；commit `f1ba627 refactor(skills): 统一 LLM History 为 Chat Completions 格式` 迁移到新格式时，**未把该防御迁移过来** |

**Root Cause 2**: `_history_to_responses_input` 新 Chat Completions 格式分支（第 337-357 行）缺少 `3c0a188` 在旧 type-based 分支加的"预扫 known_call_ids → 过滤孤立 function_call_output"防御。

**Root Cause Chain 2**:
症状（Responses API 400） → input 有孤立 function_call_output → history 里 tool 消息无对应 assistant.tool_calls → 新格式分支按序转换未校验 → **3c0a188 防御未迁移到新格式**

---

## 两者级联关系

- 问题 1 是**本次触发路径**（LLM 被放行 `mcp.servers.list` → runner 拒绝 → 异常处理路径留下孤立 function_call）
- 问题 2 是**防御漏洞**（任何"LLM 看得见但执行被拒/失败"场景都可能留下孤立 call_id，新格式分支无兜底）
- 两个问题**必须一起修**：仅修 1 消除本次症状但不防护未来；仅修 2 是兜底但用户仍然无法完成 MCP 安装

---

## 影响范围扫描

### 同源问题（需同步修复）

| 文件 | 位置 | 模式 | 修复动作 |
|------|------|------|----------|
| `octoagent/packages/skills/src/octoagent/skills/runner.py` | L410-414 | 白名单校验缺 `is_mcp` 特判 | 加入与 `litellm_client:152-157` 对称的 runtime-exempt 判断 |
| `octoagent/packages/skills/src/octoagent/skills/litellm_client.py` | L337-357 | 新格式分支缺 call_id 配对防御 | 参照 `3c0a188` 对旧格式的做法：预扫 known_call_ids，转换时过滤孤立 function_call_output |

### 类似模式（需评估）

| 文件 | 位置 | 模式 | 评估结果 |
|------|------|------|----------|
| `octoagent/packages/skills/src/octoagent/skills/compactor.py` | 全文 | 3c0a188 已修过压缩成对；是否在 Chat Completions 格式下仍 robust？ | **待评估**；Plan 阶段快速确认，若有同源问题则一并修 |
| `litellm_client.py` 旧 type-based 分支（L366-385） | 压入 function_call_output 分支 | 仍有原 3c0a188 的 known_call_ids 预扫逻辑 | **保留现状**（兜底分支，虽然用户已不走此路径） |

### 同步更新清单

- **调用方**：无（`_execute_tool_calls` 和 `_history_to_responses_input` 都是内部静态函数，无直接外部调用者需要更新）
- **测试**：
  - `tests/test_runner.py` / `test_runner_integration.py`：新增白名单 + `mcp.*` 特判正向测试
  - `tests/test_litellm_client.py`：新增孤立 function_call_output 过滤测试
- **文档**：不更新（内部实现细节）

---

## 修复策略

### 方案 A（推荐）：对称修复 + 公共函数提炼

**变更点 1 —— 提炼公共函数**：
- 在 `models.py` 或等价位置新增 `is_runtime_exempt_tool(tool_name: str, tool_group: str) -> bool`
- 封装 `is_mcp` 判断：`tool_group == "mcp" and tool_name.startswith("mcp.") and "." in tool_name[4:]`
- `litellm_client.py:152-157` 和 `runner.py:_execute_tool_calls` 两处都调用该函数

**变更点 2 —— runner.py 白名单校验加 is_mcp 放行**：
```
for call in tool_calls:
    if allowed_tool_names and call.tool_name not in allowed_tool_names:
        # 查询 tool_group，若 is_runtime_exempt_tool 则放行
        tool_meta = await self._tool_broker.get_tool_meta(call.tool_name)
        if tool_meta and is_runtime_exempt_tool(call.tool_name, tool_meta.tool_group):
            continue
        raise SkillToolExecutionError(...)
```

**变更点 3 —— litellm_client.py 新格式分支加 call_id 配对校验**：
```
# 在 _history_to_responses_input 开头预扫
known_call_ids: set[str] = set()
for message in history:
    # 新格式：assistant.tool_calls
    if message.get("role") == "assistant":
        for tc in message.get("tool_calls") or []:
            cid = str(tc.get("id", "")).strip()
            if cid:
                known_call_ids.add(cid)
    # 旧格式：type=function_call
    elif str(message.get("type", "")).strip() == "function_call":
        cid = str(message.get("call_id", "")).strip()
        if cid:
            known_call_ids.add(cid)

# role == "tool" 分支：过滤孤立
if role == "tool":
    call_id = str(message.get("tool_call_id", "")).strip()
    if call_id and call_id in known_call_ids:  # <— 新增 in 检查
        items.append({...})
    continue
```

### 方案 B（最小变更）：不提炼公共函数

- `is_mcp` 表达式在 runner 和 litellm 各写一遍
- 优点：改动行数最少
- 缺点：未来漂移风险（如 tool_group 扩展）又会重现本次 bug

**推荐方案 A**：用户的 CLAUDE.md "架构整洁优先" / "不要把最小改动当默认目标" 明确要求消除这种重复逻辑。

---

## Spec 影响

**需要更新的 spec**：无

- 两个 bug 都是实现一致性问题，不涉及任何对外承诺或架构契约变更
- `docs/blueprint/` 无相关章节需调整

---

## 修复范围复核

- 涉及文件：2 个（runner.py、litellm_client.py） + 可能 1 个公共位置（models.py）+ 2 个测试文件
- 涉及模块：`skills` 包单模块
- **明确在 fix 模式范围内**（不触发范围过大检测阈值 >10 文件 或 >3 模块）
