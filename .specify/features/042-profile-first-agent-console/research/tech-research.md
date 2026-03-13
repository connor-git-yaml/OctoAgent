# Tech Research: Feature 042 — Profile-First Tool Universe + Agent Console Reset

## 1. 当前代码基线

## 1.1 默认聊天没有把 041 的 Root Agent profile 接到首跳

证据：

- `octoagent/frontend/src/hooks/useChatStream.ts`
- `octoagent/packages/policy/src/octoagent/policy/models.py`
- `octoagent/apps/gateway/src/octoagent/gateway/routes/chat.py`

现状：

- 前端聊天请求只发送 `message + task_id`
- `ChatSendRequest` 也只有 `message` 和 `task_id`
- chat route 负责创建/复用 task，但不传 `agent_profile_id`

结论：

- 041 已经有 `worker_profiles` 与显式 launch 主链，但普通 chat 首跳仍无法显式指定 Root Agent profile
- 这导致“创建了 Root Agent”与“默认聊天真的按它运行”之间仍然有断层

## 1.2 当前 LLM 看到的是运行时裁剪后的 `selected_tools_json`

证据：

- `octoagent/apps/gateway/src/octoagent/gateway/services/delegation_plane.py`
- `octoagent/apps/gateway/src/octoagent/gateway/services/capability_pack.py`
- `octoagent/apps/gateway/src/octoagent/gateway/services/llm_service.py`

现状：

- `DelegationPlane._handle_tool_index_select()` 会先调用 `CapabilityPackService.select_tools()`
- `CapabilityPackService.select_tools()` 会把 `tool_groups / worker_type / tool_profile` 注入 `ToolIndexQuery`
- `prepare_dispatch()` 最终把 `selection.selected_tools` 写进 `selected_tools_json`
- `LLMService` 只把这份 `selected_tools_json` 暴露给 inline skill / 模型

结论：

- 当前系统是“先帮模型挑好工具，再让模型用”
- 只要前面的选择器猜偏，模型就没有自救空间

## 1.3 `agent_profile` 与 `worker_profile` 已经存在，但工具解析仍然没有 profile-first

证据：

- `octoagent/apps/gateway/src/octoagent/gateway/services/agent_context.py`
- `octoagent/packages/core/src/octoagent/core/models/agent_context.py`
- `octoagent/apps/gateway/src/octoagent/gateway/services/control_plane.py`

现状：

- 主 Agent 已有 `AgentProfile`
- 041 也已正式引入 `WorkerProfile`
- `ControlPlane` 已能输出 `worker_profiles` canonical resource
- `WorkerProfile` 本身已包含 `tool_profile / default_tool_groups / selected_tools`

结论：

- 数据模型已经足够表达“Profile 决定能力边界”
- 真正缺的是：把运行时工具解析从 `tool selection first` 改成 `profile-first universe`

## 1.4 delegation 工具本身也会被当前选择链路隐藏

证据：

- `octoagent/apps/gateway/src/octoagent/gateway/services/capability_pack.py`
- `octoagent/packages/tooling/src/octoagent/tooling/tool_index.py`

现状：

- `subagents.spawn`、`work.split`、`subagents.steer` 都是正式工具
- 但 `subagents.spawn` 是 `ToolProfile.STANDARD`
- 如果当前 worker/profile 走的是 `minimal`，它就可能根本进不了模型可见工具集

结论：

- 这不是“模型不会用 subagent”，而是“当前链路经常根本不把 delegation 核心工具给它看”

## 1.5 当前 Agent 页面信息架构仍然过于系统内部导向

证据：

- `octoagent/frontend/src/pages/AgentCenter.tsx`
- `octoagent/frontend/src/pages/ControlPlane.tsx`

现状：

- `AgentCenter` 同时承载 archetype、profile、runtime、studio、治理信息
- `ControlPlane` 又重复展示 runtime lineage 与 worker/profile 数据
- 术语层里同时出现 Butler、Worker、Profile、Work、Template、Capability Pack、Runtime 等多个视角

结论：

- 技术上已经可以展示更多信息，但 IA 不稳定，导致用户需要先理解系统内部模型

## 2. 参考实现的可迁移点

## 2.1 Agent Zero：profile 决定工具宇宙，系统 prompt 直接暴露工具

证据：

- `_references/opensource/agent-zero/python/extensions/system_prompt/_10_system_prompt.py`
- `_references/opensource/agent-zero/python/tools/call_subordinate.py`
- `_references/opensource/agent-zero/python/helpers/subagents.py`
- `_references/opensource/agent-zero/python/helpers/skills.py`

可迁移点：

- 当前 Agent 的工具列表在 system prompt 层就是显式可见的
- subordinate 可以按 `profile` 创建，而不是固定 worker 类型
- skill 级别有 `allowed_tools`，但那是边界约束，不是每轮 top-k 裁剪
- profile 支持 default/user/project 多层来源合并

不应照搬的点：

- Agent Zero 的很多事实源来自 prompt 文件与目录，不适合直接替代 OctoAgent 现有的 control plane / store / audit 链

## 2.2 Anthropic / OpenAI 官方资料：工具定义与 handoff 都应成为稳定能力，而不是猜测产物

在线补充证据：

- Anthropic tool use docs：`tools` 参数会被拼进专用 system prompt，工具定义质量直接影响调用表现
- Anthropic《Writing effective tools for AI agents》：工具描述和 eval 驱动迭代比“隐式猜测工具”更可靠
- OpenAI《A practical guide to building agents》：handoff 本质上也是一种 tool，且多 Agent 设计要依赖 evals 建立基线

对 042 的启发：

- “这个 Agent 可以用哪些核心工具”应该是稳定输入
- “要不要发现更多长尾工具”才是运行时附加能力
- delegation/handoff 工具不该被隐藏在模糊 heuristic 后面

## 3. 推荐技术方向

### D1. 引入 `Profile-First Tool Universe` 解析层

新增一个正式解析步骤：

- 输入：
  - `agent_profile_id`
  - `requested_worker_profile_id`（如有）
  - project policy
  - runtime readiness
- 输出：
  - `effective_profile_id`
  - `effective_worker_profile_id`
  - `effective_tool_universe`
  - `tool_resolution_mode`
  - `tool_resolution_warnings`

这一层的职责是先回答：

- 当前是谁
- 他理论上拥有什么核心工具
- 其中哪些当前 unavailable / degraded

### D2. `ToolIndex` 降级为“发现器”，不再做默认聊天主闸门

保留 `ToolIndex`，但改变角色：

- **主链路**：profile 固定工具宇宙
- **附加链路**：tool discovery / long-tail search / UI 推荐 / explainability

也就是说：

- 核心工具不再依赖 top-k semantic selection
- 长尾工具仍可通过 `ToolIndex` 或 catalog search 被发现

### D3. `selected_tools_json` 兼容保留，但语义升级

为了兼容现有 LLMService / runtime truth：

- 仍保留 `selected_tools_json`
- 但它不再表示“语义猜出来的 5 个工具”
- 而应表示“本次运行实际挂载给模型的核心工具集”

同时新增：

- `effective_tool_universe_json`
- `tool_resolution_mode`
- `tool_resolution_trace`

供 Control Plane 和 Agent 页面解释使用。

### D4. 普通 Chat 请求要能显式绑定 profile

推荐改动：

- `ChatSendRequest` 新增可选 `agent_profile_id`
- 前端 chat surface 新增“当前 Agent”绑定
- 若未显式指定，则回退到：
  1. 当前 session 绑定 profile
  2. project 默认 profile
  3. system default Butler profile

### D5. delegation 核心工具需要从“可选发现”升为“稳定挂载”

对于 Butler / 可委派 Root Agent，以下工具应进入核心工具带，而不是等待 ToolIndex 命中：

- `workers.review`
- `subagents.spawn`
- `subagents.list`
- `subagents.steer`
- `work.split`
- `session.status`
- `project.inspect`
- `work.inspect`

是否可调用仍由：

- `tool_profile`
- policy
- readiness

决定，但不应再由语义猜测来决定“看不看得见”。

### D6. UI 信息架构重组为“一个主工作台 + 一个深度控制面”

推荐把职责收敛为：

- `AgentCenter`
  - 用户的主 Agent 工作台
  - 负责 Root Agent 管理、默认 Agent、当前工作、静态配置、可用能力
- `ControlPlane`
  - 深度排障与审计面
  - 负责 trace、runtime lineage、raw projection、advanced governance

而不是让两个页面都半懂不懂地展示 profile/runtime。

### D7. 迁移策略

1. 保留 `selected_worker_type` 兼容字段  
2. 新老 work 同时支持  
3. Root Agent profile 继续复用 041 的 canonical resources/actions  
4. Agent 页面先双显：
   - `Profile`
   - `Archetype`
   - `Tool Access`
   - `Runtime State`

## 4. 技术风险

1. 如果直接删除 `ToolIndex` 主链而不补 explainability，会失去当前的工具选择审计入口。
2. 如果把所有可见工具一次性全挂载给模型，可能导致 prompt 过长与 tool call 质量下降。
3. 如果 Agent 页面只改视觉不改 IA，042 只会把更多概念塞进当前混乱页面。
4. 如果 chat 首跳仍然不接 `agent_profile_id`，042 只会再次出现“Profile 创建了，但默认聊天没在用”的断层。

## 5. 推荐 MVP 切片

### Slice 1

- chat/profile binding
- profile-first core tool universe
- delegation core tools 常驻

### Slice 2

- tool resolution explainability
- Agent 页面 IA 重组
- Root Agent 当前默认/当前工作/当前能力可视化

### Slice 3

- tool discovery side panel
- acceptance matrix / eval harness
- advanced inspector 与 control-plane 联动
