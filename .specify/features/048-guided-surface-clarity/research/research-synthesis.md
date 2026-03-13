# Research Synthesis - Feature 048

## 综合判断

这不是“文案润色”问题，而是普通用户 surface 仍沿用控制台心智的问题。

当前系统已经有：

- canonical setup / diagnostics / work / context resources
- Butler -> Worker runtime truth
- A2A conversations / messages

缺的是：

- 首屏优先级排序
- 用户语言翻译层
- 等待态产品化

## 设计方向

### 1. Home 改成“单一结论 + 单一主行动”

不再让用户自己解读 `action_required / degraded / total_pending / fragment_count`。

### 2. Settings 改成“最少必要配置入口”

继续保留 044 的结构刷新方向，但首屏更强地强调：

- 当前模式
- 最少缺什么
- 配完去哪里验证

### 3. Chat 增加“正在处理”和“内部协作进度”

不是展示 raw thinking，而是展示：

- Butler 已接手
- 已委托专门角色
- 正在取回结果
- 正在整理答复

### 4. raw diagnostics 全部退后

普通 surface 不再原样显示：

- raw status enum
- object stringification
- context recent summary
- tool/search traces

需要时才进入 `Advanced`。
