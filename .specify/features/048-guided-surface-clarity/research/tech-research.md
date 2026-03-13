# Tech Research - Feature 048

## 当前代码入口

- 首页：
  - `octoagent/frontend/src/domains/home/HomePage.tsx`
  - `octoagent/frontend/src/domains/home/readiness.ts`
- 设置首屏：
  - `octoagent/frontend/src/domains/settings/SettingsOverview.tsx`
- 聊天与等待态：
  - `octoagent/frontend/src/pages/ChatWorkbench.tsx`
  - `octoagent/frontend/src/hooks/useChatStream.ts`

## 关键发现

### 1. 首页 hero 直接绑定 raw readiness label

`HomePage` 直接用 `computeReadinessLabel()` 的 `label/summary` 作为 `PageIntro` 标题和摘要。  
当前 readiness 逻辑只区分：

- `先完成基础配置`
- `再检查一次设置`
- `系统需要检查`
- `有待你确认的事项`
- `可以开始使用`

问题：
- 语义过粗
- 不包含具体下一步
- 和页面下方卡片的 raw status 混在一起

### 2. 首页卡片统计口径偏内部

- `当前工作` 直接用 `delegation.works.length`
- `进行中` 用固定状态集合统计
- `待你确认` 来自 operator summary
- `记忆摘要` 直接展示 sor/fragments/proposals

问题：
- 历史累计与当前状态混合
- 没有说明这些数字对用户意味着什么
- `记忆摘要` 对普通用户价值很低

### 3. 首页存在 raw object / raw summary 泄漏

- channel summary 使用 `String(value)`
- recent summary 直接显示 `latestContextSummary`

这会导致：
- `telegram: [object Object]`
- tool/search/runtime 摘要直接上屏

### 4. Settings 首屏仍以配置结构为中心

当前 `SettingsOverview` 首屏是：

- 系统连接与默认能力
- 概览 chips
- 配置状态 / 接入模式 / providers / alias / memory / policy 卡片

问题：
- 仍然在解释结构
- 没有形成“最少必要配置 -> 回聊天验证”的闭环

### 5. 聊天等待态只到 boolean `streaming`

`useChatStream` 只有 `streaming/restoring/error/taskId`。  
`ChatWorkbench` 在发送中只会 disabled 输入框并把按钮改成“发送中”。

问题：
- 没有分阶段等待态
- 没有协作中态
- 没有面向普通用户的进度反馈

### 6. 协作数据其实已经在前端

`ChatWorkbench` 已可读取：

- `a2a_conversations`
- `a2a_messages`
- `work.runtime_summary`
- `worker session / recall frames`

这意味着：
- 不需要先新增 backend 才能做第一版进度面板
- 更适合把现有事实源翻译成高层阶段
