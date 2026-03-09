# Product Research: Feature 032 — OpenClaw Built-in Tool Suite + Live Graph/Subagent Runtime

## 结论

1. 032 的核心不是“把 built-in tools 数量做多”，而是把内置工具、subagent、graph、child work 变成用户真正可用的产品面。
2. OctoAgent 当前缺的不是 capability framework，而是 **live runtime truth**：用户需要知道某个工具能不能用、某个 subagent 是否真的跑起来、某个 graph 是否真的是 graph backend。
3. OpenClaw 的优势不只是工具多，而是工具、session、subagent、browser/web、gateway/nodes 等能力都已经形成“可以被 agent 和 operator 直接调用”的统一产品面。
4. 032 必须显式回应用户的担心：不能继续交付“有代码、有一些测试，但没有贯通到用户入口”的伪实现。

## 当前产品缺口

当前 OctoAgent 已经有 030 的第一阶段基础，但用户视角仍有三类空洞：

- **工具面太薄**：默认只看得到极少数 inspect/list 工具，距离真实日常使用差距很大。
- **runtime truth 不清**：`graph_agent` / `subagent` 已经出现在 route reason 和 target kind 里，但对 operator 来说仍然不够可信。
- **child work 不完整**：create / merge 有雏形，但 split 与主 Agent 的正式 child-work lifecycle 没有形成完整产品语义。

## OpenClaw 对标结论

参考：

- `_references/opensource/openclaw/src/agents/tools/`
- `_references/opensource/openclaw/docs/tools/skills.md`
- `_references/opensource/openclaw/docs/tools/slash-commands.md`

OpenClaw 的 built-in tool 面具备几个对 OctoAgent 直接有价值的特点：

### 1. Tool families 是真正给用户用的，不是内部私有工具

代表工具：

- `sessions_spawn`
- `sessions_list`
- `session_status`
- `subagents`
- `web_fetch`
- `web_search`
- `browser`
- `gateway`
- `cron`
- `nodes`
- `pdf`
- `image`
- `tts`
- `memory`

这说明 032 不应该只加“系统内部好看的工具元数据”，而是要补成真正可被 agent/operator 消费的工具面。

### 2. session / subagent 是第一类产品对象

OpenClaw 把 `sessions_spawn` 做成正式工具，而不是只在 runtime 内部偷偷创建子线程。这一点对 OctoAgent 非常关键：

- subagent 必须有显式 spawn 语义
- spawn 结果必须有稳定 session / run 标识
- operator 必须能看见 child runtime 的生命周期

### 3. web/browser/media/doc 工具族是高频 built-in 能力

这类能力不是“插件生态后续再说”，而是用户默认期望的日常工具面。OctoAgent 如果继续只提供 inspect/list，会导致主 Agent 的默认工具面长期偏弱。

## 用户真正关心的体验

### A. 我能不能马上用

对用户来说，判断 built-in tool 是否交付，不是看仓库里有没有 handler，而是看：

- 能不能从主 Agent / Worker / CLI / Web 至少一个真实入口调用
- 缺依赖时有没有明确提示
- 执行结果是否能回到当前会话或 control plane

### B. 这是不是一个真的 subagent / graph

用户不会因为界面上写了 `graph_agent` 就相信它是 graph runtime，也不会因为 work target 写了 `subagent` 就相信真的起了 child runtime。

032 要解决的就是这种“术语和真实能力不一致”的信任问题。

### C. 主 Agent 是否真的会拆工作

如果系统宣称有 multi-worker / delegation，但主 Agent 不能：

- 正式创建 child work
- 把一项工作拆成多个 child works
- 在 child works 完成后 merge 回父 work

那这个产品面仍然不够成立。

## 产品目标拆解

### 1. Built-in Tool Suite

最小产品面至少应包含：

- tool family 分类
- tool availability / install hint / degraded reason
- real entrypoint binding
- tool-level audit / event visibility

### 2. Live Runtime Truth

032 必须把以下状态显式化：

- 这是一个真实 graph run 还是普通 worker run
- 这是一个真实 subagent session 还是仅有 metadata
- 当前 child work 是否已 split、assigned、running、merged、cancelled

### 3. Anti-Fake Gates

产品验收不能只看单元测试和 schema 存在与否，必须看：

- 入口是否真实可达
- runtime 是否真实运行
- control plane 是否能展示状态
- degraded path 是否真实存在

## 范围判断

### 032 现在必须覆盖

- OpenClaw 风格的高价值 built-in tool families
- pydantic graph / subagent / child work lifecycle 的真实可用性
- control plane 的 tool/runtime truth 面

### 032 现在不该覆盖

- channel action packs
- remote nodes / companion surfaces
- marketplace
- 新控制台框架

## 产品决策

- 032 放在 M4，作为 M3 之后第一个“体验深化但必须真实可用”的能力包。
- 032 的成功标准不是工具数量本身，而是“工具 + runtime + operator surface” 是否真正打通。
- 032 必须把“Graph / subagent / worker split-merge” 从术语层推进到产品层。
