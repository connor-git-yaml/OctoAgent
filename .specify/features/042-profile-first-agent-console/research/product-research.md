# Product Research: Feature 042 — Profile-First Tool Universe + Agent Console Reset

## 结论

042 的核心不是“修 weather”或“再调一下 tool index”，而是把默认 Agent 体验从“系统猜一轮你可能要什么工具”改成“先确定这个 Agent 是谁、天然拥有什么能力，再让模型自己在边界内行动”。

对用户而言，这个 Feature 的价值主要体现在三件事：

1. 默认聊天终于更像一个真的 Agent，而不是一个经常解释自己为什么做不到的问答框。
2. 用户创建的 Root Agent/Profile 不再只存在于 Agent 页面或显式 launch 流程里，而是真正进入普通聊天主链。
3. Agent 页面从“术语堆砌的控制台”变成“我有哪些 Agent、它们现在在干什么、为什么能/不能做某件事”的可理解产品界面。

## 用户痛点

## 1. 默认聊天感知不到用户已经配置好的 Agent 能力

当前用户即使已经在 041 中创建了 Root Agent profile，普通 `/chat` 入口仍然主要依赖旧的 `worker_type + tool selection` 路径。对用户来说，表面现象是：

- 我明明已经配置过 Agent，为何默认聊天还是像个“裸 Butler”
- 我问的是很简单的现实世界问题，系统却先解释限制，而不是先尝试调用现有工具
- 我不知道是模型不行、工具没配、还是路由压根没走到我的 profile

这会直接伤害系统的第一印象。

## 2. 工具边界是系统内部概念，不是用户能理解的产品概念

当前系统内部用 `tool_profile / tool_groups / selected_tools / tool_selection / fallback` 解释能力来源，但用户真正关心的是：

- 这个 Agent 平时就应该能查网页吗
- 它为什么这次没有联网
- 它什么时候会自己拆 worker / 起 subagent
- 这是不是我当前项目默认 Agent 的能力

如果这些问题只能去 `ControlPlane` 或靠读日志理解，产品就还是 operator-first，而不是 user-first。

## 3. Agent 页面对象分层还不够清楚

041 已经补齐了 `Root Agent Profiles + Profile Studio` 的主链，但当前页面仍然偏“系统视角”：

- starter template、正式 profile、运行 work 虽然比以前清楚，但依旧需要用户理解很多内部术语
- 页面同时承载模板、治理、运行态、控制面信息，信息密度高但阅读路径不稳定
- 用户缺少一个“当前默认 Agent / 当前工作 / 当前可用能力”非常直白的主视图

## 参考实现的产品启发

## 1. Agent Zero：能力边界更像“Agent 自带工具带”，不是每轮临时裁剪

参考：

- `_references/opensource/agent-zero/README.md`
- `_references/opensource/agent-zero/webui/components/sidebar/left-sidebar.html`
- `_references/opensource/agent-zero/webui/components/sidebar/chats/chats-list.html`
- `_references/opensource/agent-zero/webui/components/sidebar/tasks/tasks-list.html`

可借鉴点：

- 默认工具宇宙小而稳定，用户更容易形成“这个 Agent 天然能做什么”的心智模型
- 侧边栏把 Chats / Tasks / Quick Actions 分开，信息架构非常直接
- Subagents/profile 被视作长期配置资产，不是一次性 runtime 细节
- UI 更强调“正在进行什么”和“我下一步能做什么”

不应照搬的点：

- Agent Zero 的自由度很高，但对普通用户解释性不足；OctoAgent 仍需保留治理和 explainability
- 它很多能力依赖 prompt/file layering，OctoAgent 需要继续保持 control plane / audit / policy 的正式对象链

## 2. OpenClaw：管理面首先是产品，其次才是控制面

参考：

- `_references/opensource/openclaw/docs/web/dashboard.md`
- `_references/opensource/openclaw/apps/macos/Sources/OpenClaw/SettingsRootView.swift`

可借鉴点：

- Dashboard 被定义成 admin/control surface，但入口非常清晰：连接、状态、下一步动作
- 设置页使用稳定的信息架构分区，而不是一个“全能大页”
- 管理面强调 “readiness / auth / instances / sessions / cron / permissions”，用户更容易找到入口

对 042 的启发：

- Agent 页面要从“系统能力展示”切换成“用户的 Agent 工作台”
- IA 需要稳定的主导航和次级 inspector，不要让所有概念平铺
- 术语需要翻译成用户世界里的对象，例如：
  - `Root Agent`
  - `默认 Agent`
  - `正在工作`
  - `可用能力`
  - `为什么这次没用到`

## 3. UI/UX Pro Max 设计系统基线

基于 `ui-ux-pro-max` 检索：

- Query: `AI agent control plane dashboard data-dense professional operations console`
- UX domain: `dashboard agent console information architecture accessibility`

推荐设计方向：

- **整体风格**：`Data-Dense Dashboard + Drill-Down`
- **视觉语义**：技术蓝为主色，琥珀作为需要关注的高亮色
- **排版方向**：`Fira Sans + Fira Code` 这类“控制台感但仍然可读”的组合适合 Agent Console
- **交互原则**：
  - 一屏同时看见静态配置和动态状态
  - 主列表 + 详情 + 右侧 inspector 的三栏结构优于多层跳转
  - 大量使用 badge、状态标签、diff、warning callout，而不是大段说明文字
- **必须规避**：
  - 只靠颜色表达状态
  - 图标按钮缺少 aria label
  - nav-heavy 页面没有 keyboard skip link
  - 用大留白掩盖信息结构不清的问题

## 产品目标收敛

### 目标一：让普通聊天默认继承当前 Agent 身份

用户不应该在 Agent 页面里有一个“配置好的 Root Agent”，而在 Chat 页面里又回到“没有能力的泛化 Butler”。

### 目标二：让“能做什么”成为 profile 的稳定属性

用户需要知道：

- 哪些工具是这个 Agent 的稳定能力
- 哪些是运行时按需发现的长尾能力
- 哪些需要审批或 readiness 条件

### 目标三：让 Agent 页面首先回答 3 个问题

1. 我现在默认在用哪个 Agent  
2. 它现在在做什么  
3. 它为什么能/不能做这件事

## MVP 范围建议

### Must-have（MVP）

- 默认聊天进入 `profile-first` 能力解析，而不是先走硬 top-k tool selection
- Butler / Root Agent 能稳定看到 delegation 核心工具，不再因为工具裁剪而“想委派却委派不了”
- Agent 页面改成清晰的三段信息架构：
  - Root Agents
  - Current Work
  - Inspector / Tool Access
- UI 上能解释“本次工具为什么可用/不可用”

### Nice-to-have（二期）

- 会话级 profile 切换器
- 长尾工具搜索与收藏
- profile effectiveness / eval 分数面板

### Future（远期）

- 多实例 Root Agent
- 团队级 Agent Library
- Tool usage recommendation / optimization assistant

## 产品建议

- 042 应定义为 `Profile-First Tool Universe + Agent Console Reset`
- 重点不是发明更多 worker 类型，而是把默认聊天主链、tool governance、AgentCenter IA 一起理顺
- 前端交付不应只是“换皮”，而应明确减少概念数量、减少跨页跳转、减少用户理解内部术语的负担
