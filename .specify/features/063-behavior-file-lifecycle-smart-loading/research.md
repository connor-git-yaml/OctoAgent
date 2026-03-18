# Feature 063 调研报告：行为文件生命周期与智能加载

> 本文档为 Feature 063 的前期调研产出，基于 OctoAgent、OpenClaw、Agent Zero、Claude Code 四个系统的源码级对比分析。

---

## 一、调研范围

| 系统 | 调研方式 | 源码位置 |
|------|---------|---------|
| OctoAgent | 源码阅读 | 当前仓库 |
| OpenClaw | 源码 + Connor 使用快照 | `_references/opensource/openclaw/` + `_references/openclaw-snapshot/` |
| Agent Zero | 源码阅读 | `_references/opensource/agent-zero/` |
| Claude Code | 官方文档 + Web 搜索 | 在线 |

---

## 二、OctoAgent 现状

### 2.1 行为文件体系（9 个文件）

定义位置：`packages/core/src/octoagent/core/behavior_workspace.py`

#### System Shared（实例级，所有 Agent 共享）

| 文件 | 字符预算 | 用途 |
|------|---------|------|
| `AGENTS.md` | 3200 | 行为总约束 |
| `USER.md` | 1800 | 用户长期偏好 |
| `TOOLS.md` | 3200 | 工具与边界 |
| `BOOTSTRAP.md` | 2200 | 初始化引导 |

#### Project Shared（项目级，项目内所有 Agent 共享）

| 文件 | 字符预算 | 用途 |
|------|---------|------|
| `PROJECT.md` | 2400 | 项目语境 |
| `KNOWLEDGE.md` | 2200 | 知识入口 |

#### Agent Private（Agent 级，每个 Agent 独有）

| 文件 | 字符预算 | 用途 |
|------|---------|------|
| `IDENTITY.md` | 1600 | 身份补充 |
| `SOUL.md` | 1600 | 表达风格 |
| `HEARTBEAT.md` | 1600 | 运行节奏 |

### 2.2 目录结构

```
{project_root}/
  behavior/
    system/                              # System Shared
      AGENTS.md / USER.md / TOOLS.md / BOOTSTRAP.md
      *.local.md                         # local override
    agents/{agent_slug}/                 # Agent Private
      IDENTITY.md / SOUL.md / HEARTBEAT.md
      *.local.md
  projects/{project_slug}/
    behavior/                            # Project Shared
      PROJECT.md / KNOWLEDGE.md
      *.local.md
      agents/{agent_slug}/              # Project-Agent overlay
        IDENTITY.md / SOUL.md / ...
        *.local.md
    workspace/ data/ notes/ artifacts/
    behavior/instructions/README.md      # 项目说明
    project.secret-bindings.json         # secret 绑定元数据
```

### 2.3 Overlay 优先级（9 级，低→高）

```
default_template → system_file → system_local → agent_file → agent_local
→ project_file → project_local → project_agent_file → project_agent_local
```

每个 file_id 只取优先级最高的那一个来源。`.local.md` 用于本地覆盖不入版本管理。

### 2.4 创建路径

| 触发点 | 函数 | 创建的文件 |
|--------|------|-----------|
| Gateway 启动 | `ensure_filesystem_skeleton()` | System Shared + Project Shared |
| 启动 bootstrap | `materialize_agent_behavior_files()` | Agent Private |
| 创建新 Project | `materialize_project_behavior_files()` | Project Shared + 目录骨架 |
| Control Plane | `ensure_filesystem_skeleton()` + `materialize_agent_behavior_files()` | 全部 |
| CLI | `octo behavior init` | 全部 |

### 2.5 更新机制

- Agent 通过 `behavior.read_file` / `behavior.write_file` 工具读写
- 写入前校验路径 + 字符预算
- 默认 `review_mode=review_required`：第一次返回 proposal，用户确认后第二次才写入
- CLI: `octo behavior edit` / `octo behavior apply`

### 2.6 消费路径

```
resolve_behavior_workspace()  →  resolve_behavior_pack()
  →  render_behavior_system_block()  →  注入 LLM system prompt
  →  build_behavior_tool_guide_block()  →  system prompt 工具指南
  →  build_behavior_system_summary()  →  API / 前端
  →  build_behavior_slice_envelope()  →  传递给 Worker 的 shared 子集
```

### 2.7 SKILL.md 体系

三级发现（优先级低→高）：

| 来源 | 路径 | 可卸载 |
|------|------|--------|
| Builtin | `{repo_root}/skills/` | ❌ |
| User | `~/.octoagent/skills/` | ✅ |
| Project | `{project_root}/skills/` | ✅ |

两阶段加载：启动时注入 name+description 摘要 → LLM 主动 `skills load` 获取完整 body。

### 2.8 现有问题总结

| 问题 | 影响 | 严重度 |
|------|------|--------|
| BOOTSTRAP.md 永久存在 | 完成 onboarding 后仍每次注入，浪费 token | HIGH |
| 全量注入无差异化 | Worker/Subagent 收到全部 9 个文件，大量不相关 | HIGH |
| 无自动压缩/清理 | 行为文件只增不减，长期运行后膨胀 | MEDIUM |
| 行为文件无条件加载 | 无法按任务/项目/阶段智能裁剪 | MEDIUM |

---

## 三、竞品分析

### 3.1 OpenClaw

**核心亮点：Bootstrap 自毁机制**

```
创建 workspace → 播种 BOOTSTRAP.md
→ Agent 按指令完成 onboarding 对话
→ Agent 自行删除 BOOTSTRAP.md（模板最后写："Delete this file."）
→ 系统检测删除 → 标记 onboardingCompletedAt
→ 此后永不再创建
```

状态跟踪：`workspace-state.json` 中的 `bootstrapSeededAt` / `onboardingCompletedAt`。
Legacy 兼容：如果 IDENTITY.md/USER.md 已被修改但 state 缺失，视为已完成。

**分层加载策略**：
- Main session → 全部文件
- Subagent/Cron → 只加载 `{AGENTS, TOOLS, SOUL, IDENTITY, USER}`（不含 HEARTBEAT/MEMORY/BOOTSTRAP）
- 心跳轻量模式 → 仅 HEARTBEAT.md

**Context Compactor**（用户自建 cron Skill）：
- 每天 03:00 执行，阈值 25KB
- 压缩前备份到 `archive/`
- `[🔒 不压缩]` 标记保护关键 section
- 详情下沉到 `memory/system/` 子目录，顶层只保留骨架+引用

**行为文件清单**：AGENTS.md / SOUL.md / IDENTITY.md / USER.md / TOOLS.md / HEARTBEAT.md / BOOTSTRAP.md / MEMORY.md / BOOT.md / TASKS.md / TODO.md / WORKFLOW_AUTO.md

**单文件上限**：20,000 字符 | **总量上限**：150,000 字符 | **截断策略**：70% 头 + 20% 尾

### 3.2 Agent Zero

**核心亮点：6 级路径搜索 + Profile 覆盖**

```
搜索优先级（高→低）：
1. usr/projects/<project>/.a0proj/agents/<profile>/prompts/
2. usr/projects/<project>/.a0proj/prompts/
3. usr/agents/<profile>/prompts/
4. agents/<profile>/prompts/
5. usr/prompts/
6. prompts/                              # 默认
```

Agent profile 只需放置要覆盖的文件，其他自动从上级继承。

**behaviour.md 动态调整**：
- Agent 调用 `behaviour_adjustment` 工具
- 读取当前规则 + 调整文本 → utility LLM 合并 → 写回文件
- **插入到 system prompt 最前面**（`insert(0, ...)`，优先级最高）
- 删除文件则回退到 `behaviour_default.md`

**Extras 机制**：每次 loop 迭代刷新，分 persistent（跨轮保留）和 temporary（单轮），附加在 history 之后。

**模板引擎**：`{{key}}` 变量替换 + `{{ include "file.md" }}` 递归引入 + `{{if condition}}` 条件块 + 同名 `.py` 变量插件。

### 3.3 Claude Code

**核心亮点：多层级发现 + 条件加载 + 200 行硬限**

**CLAUDE.md 层级**：
```
企业级 → 全局用户级 (~/.claude/CLAUDE.md) → 项目级 (CLAUDE.md)
→ 项目私有 (CLAUDE.local.md) → 模块化规则 (.claude/rules/*.md)
→ 子目录级 (subdir/CLAUDE.md)
```

**条件加载（.claude/rules/）**：
```yaml
---
paths:
  - "src/api/**/*.ts"
  - "tests/**/*.test.ts"
---
# 这些规则只在操作匹配路径的文件时注入
```

**MEMORY.md 双重记忆**：
- `CLAUDE.md` = 用户编写的规则指令（"按这样做"）
- `MEMORY.md` = Claude 自动写入的学习笔记（"我发现了这些"）
- MEMORY.md **只自动加载前 200 行**，强制作为索引使用
- 详细内容分散到主题文件，按需读取

**子 Agent 记忆三级 scope**：
- `user`：`~/.claude/agent-memory/<agent>/`
- `project`：`.claude/agent-memory/<agent>/`（可提交 git）
- `local`：`.claude/agent-memory-local/<agent>/`（gitignore）

---

## 四、四框架对比矩阵

| 维度 | OctoAgent | OpenClaw | Agent Zero | Claude Code |
|------|-----------|----------|------------|-------------|
| **Bootstrap 生命周期** | ❌ 永久存在 | ✅ LLM 自删除 | ❌ 无 bootstrap | ❌ 无 bootstrap |
| **差异化加载** | ❌ 全量注入 | ✅ 按 session 类型白名单 | ✅ Profile 覆盖继承 | ✅ 子 Agent 独立上下文 |
| **条件加载** | ❌ 无 | 心跳轻量模式 | Project + Profile 双维 | ✅ glob path-scoped rules |
| **文件大小控制** | 字符预算（1.6K-3.2K） | 单文件 20K / 总量 150K | 无限制 | 200 行硬限 |
| **自动压缩** | ❌ 无 | 用户自建 Compactor | ❌ 无 | ❌ 无 |
| **Overlay/覆盖** | 9 级 | 无覆盖（单文件） | 6 级路径搜索 | 层级合并 |
| **Memory 存储** | ✅ 结构化（SQLite/LanceDB） | MD 文件（膨胀到 580MB） | FAISS 向量 | MD 文件（200 行限制） |
| **Skill 两阶段加载** | ✅ 有 | ❌ 无 | ✅ 有 | ✅ 有 |
| **动态行为调整** | 工具写入（需审批） | 自由编辑 + Compactor | utility LLM 合并 | 自动写入 MEMORY |

---

## 五、OctoAgent 的结构性优势

1. **结构化 Memory**：SQLite + LanceDB 比 MD 文件更可控（OpenClaw 的 4574 文件/580MB 是反面教材）
2. **9 级 Overlay**：比 Agent Zero 的 6 级更精细，比 OpenClaw 的无覆盖更灵活
3. **字符预算机制**：每个文件有硬上限，从源头控制膨胀
4. **审批机制**：`review_required` 防止 Agent 静默改写关键行为文件
5. **Skill 两阶段加载**：已实现，与 Agent Zero / Claude Code 对齐

---

## 六、需要改进的方向

基于以上分析，识别出 4 个改进方向（按优先级排列）：

### P0：Bootstrap 生命周期管理

BOOTSTRAP.md 创建后永久存在，完成 onboarding 后仍每次注入浪费 token。需要借鉴 OpenClaw 的"自毁"机制，让 Agent 完成引导后标记完成，系统不再注入。

### P1：Sub-agent/Worker 差异化加载

当前 Worker 和 Subagent 收到全部 9 个行为文件。需要定义 `BehaviorLoadProfile`，按 Agent 类型裁剪：
- Butler（FULL）→ 全部 9 个
- Worker（WORKER）→ AGENTS + TOOLS + IDENTITY + PROJECT（不含 USER/SOUL/HEARTBEAT/BOOTSTRAP）
- Subagent（MINIMAL）→ AGENTS + TOOLS + IDENTITY

### P2：行为文件智能加载策略

所有行为文件无条件全量注入。需要支持按上下文（任务类型、项目阶段、工具使用）决定注入哪些文件、注入摘要还是全文。可借鉴 Claude Code 的 path-scoped rules 和 SKILL.md 的两阶段加载模式。

### P3：Behavior Compactor

行为文件只增不减。需要内置压缩机制：定期检查总大小 → LLM 压缩 → 详情下沉到 KNOWLEDGE.md → 顶层保留骨架。借鉴 OpenClaw 的 `[🔒 不压缩]` 标记。
