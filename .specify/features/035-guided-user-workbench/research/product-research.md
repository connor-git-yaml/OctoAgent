# Product Research: Feature 035 — Guided User Workbench + Visual Config Center

## 结论

035 的核心不是“把现有控制台做得更花”，而是把 OctoAgent 的默认 Web 入口从 `operator resource browser` 变成 `用户工作台`。

OpenClaw 和 Agent Zero 给出的共同启发非常一致：

1. 新用户首先需要的是一条连续路径，而不是一组分散功能；
2. 设置中心、聊天、任务、记忆必须是统一产品壳，而不是多个独立 demo；
3. 高级能力要保留，但默认不应压在新用户脸上。

## 对标观察

## 1. OpenClaw：Control UI 只是 control plane，产品是 assistant

参考：

- `_references/opensource/openclaw/README.md`
- `_references/opensource/openclaw/docs/start/wizard.md`
- `_references/opensource/openclaw/docs/web/control-ui.md`
- `_references/opensource/openclaw/docs/web/dashboard.md`

关键产品信号：

- `openclaw onboard` 被明确声明为推荐路径，负责 gateway、workspace、channels、skills 的连续引导。
- Control UI 不只是“配置页”，而是 chat、channels、sessions、cron、skills、config、debug 的统一浏览器。
- 文档反复强调 “Gateway is just the control plane — the product is the assistant”。

对 035 的直接启发：

- OctoAgent 的默认入口不能继续是底层资源词汇，而应先回答“系统现在能不能用、我接下来做什么、我在哪个会话里和助手协作”。
- 现有 control plane 可以保留，但位置应从“默认首页”下沉为“高级模式”。

## 2. Agent Zero：欢迎页、设置中心、记忆面板和聊天必须是一体的

参考：

- `_references/opensource/agent-zero/README.md`
- `_references/opensource/agent-zero/docs/developer/architecture.md`

关键产品信号：

- Quick start 极其直接：容器起来后先打开 Web UI。
- README 持续强调 settings、memory dashboard、projects、scheduler、save/load chats、实时干预。
- 版本更新记录反复围绕 welcome screen、settings、memory dashboard、scheduler redesign、file browser、message queue、process groups 展开。

对 035 的直接启发：

- 用户真正感知到的是：欢迎页是否清楚、设置是不是统一、聊天是不是可持续、记忆是不是可解释。
- OctoAgent 现在更像“有后台能力的控制台”，而不是“一个以聊天工作台为中心的个人 AI OS”。

## 3. 当前 OctoAgent 的用户感知缺口

### 3.1 默认入口还是 operator 脑回路

首页看到的是：

- `Capability`
- `Delegation`
- `Pipelines`
- `Diagnostics`
- `Imports`

这些都不是小白应该先看到的词。

### 3.2 配置流程没有被产品化

015 和 026 已经提供：

- wizard session
- config schema
- diagnostics summary
- project selector

但 Web 并没有把它们组织成“继续设置”“保存并检查”“当前卡点”的人话流程。

### 3.3 聊天不是主入口

当前 `ChatUI` 太轻，和 `TaskDetail`、`ControlPlane` 完全是三套语言。
对用户来说，系统像“后台 + 一个 demo 聊天框”，而不是以聊天为主入口的产品。

## 产品目标收敛

### 目标一：先回答“能不能用”

首页先给：

- 系统状态
- 当前 project
- 待你确认
- 当前记忆/渠道状态
- 下一步

### 目标二：配置要改成人话

不要先展示：

- raw schema
- field path
- runtime kind

而要展示：

- 主 Agent
- 工作方式
- 记忆方式
- 渠道连接

### 目标三：聊天、工作、记忆连起来

真正的用户路径应该是：

1. 看首页状态
2. 改设置
3. 发消息
4. 看系统在做什么
5. 处理确认
6. 回看记忆与结果

### 目标四：高级能力下沉，不消失

现有 `ControlPlane` 仍然非常有价值，但应该归到 `Advanced`：

- 日常使用不打扰
- 诊断时能深入

## 产品决策

- 035 是 M4 的体验深化 feature，但它不是“纯视觉升级”，而是“把 M3/M4 已有能力组织成真正可用产品”的 UI/IA 特性。
- 035 不能再产生新的后端协议；否则只会制造“一个新页面 + 一套新 API”的新碎片。
- 035 的验收必须围绕“普通用户是否能不用终端完成关键路径”。
