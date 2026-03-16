# Quickstart: 057 行为文件模板落盘与 Agent 自主更新

## 实现顺序

本特性分 5 个阶段，严格按依赖顺序实现：

### Phase 1: 模板落盘

**目标**: 系统启动时自动将 9 个默认行为文件写入磁盘

**修改**: `behavior_workspace.py` -> `ensure_filesystem_skeleton()`

**验证**:
```bash
# 删除 data 目录后启动系统
rm -rf data/behavior data/projects/default/behavior
# 启动后检查
ls data/behavior/system/  # 应有 AGENTS.md USER.md TOOLS.md BOOTSTRAP.md
ls data/behavior/agents/butler/  # 应有 IDENTITY.md SOUL.md HEARTBEAT.md
ls data/projects/default/behavior/  # 应有 PROJECT.md KNOWLEDGE.md
```

### Phase 2: 共享辅助函数

**目标**: 提取路径校验、文件读取、预算检查为可复用函数

**修改**: `behavior_workspace.py` 新增 `validate_behavior_file_path()` / `read_behavior_file_content()` / `check_behavior_file_budget()`

**验证**: 单元测试通过

### Phase 3: LLM 工具注册

**目标**: Agent 可通过对话读写行为文件

**修改**: `capability_pack.py` -> `_register_builtin_tools()` 新增 `behavior.read_file` / `behavior.write_file`

**验证**: 在 Web UI 对话中向 Agent 说「读一下我的 USER.md」，Agent 应调用 behavior.read_file

### Phase 4: System Prompt 引导

**目标**: Agent 知道何时、如何使用行为文件工具

**修改**:
- `butler_behavior.py` 新增 `build_behavior_tool_guide_block()`
- `agent_context.py` 注入 BehaviorToolGuide block

**验证**: 检查 Agent system prompt 输出中包含文件清单表和工具参数说明

### Phase 5: 修复幽灵引用

**目标**: bootstrap.answer 不再出现在 system prompt 中

**修改**: `agent_context.py` L3874

**验证**: 搜索 system prompt 输出中不包含 "bootstrap.answer"

## 关键文件清单

| 文件 | 作用 |
|------|------|
| `octoagent/packages/core/src/octoagent/core/behavior_workspace.py` | 行为文件路径/内容/预算的单一事实源 |
| `octoagent/apps/gateway/src/octoagent/gateway/services/capability_pack.py` | LLM 工具注册中心 |
| `octoagent/apps/gateway/src/octoagent/gateway/services/agent_context.py` | System prompt 组装 |
| `octoagent/apps/gateway/src/octoagent/gateway/services/butler_behavior.py` | Behavior prompt 渲染 |
| `octoagent/apps/gateway/src/octoagent/gateway/services/control_plane.py` | 前端 action handler（已有 behavior.read/write） |

## 注意事项

- `_default_content_for_file()` 是模板内容的**单一事实源**，不要在其他地方重复定义默认模板
- 路径校验必须防止 path traversal（`../`），使用 `Path.resolve()` + `is_relative_to()` 双重检查
- `behavior.write_file` 的 `confirmed` 参数默认为 `false`，这是设计意图，不是 bug
- 字符预算检查用 `len(content.strip())`，与 `_apply_behavior_budget()` 的计算方式一致
