---
feature_id: "048"
title: "Guided Surface Clarity Refresh"
created: "2026-03-14"
updated: "2026-03-14"
status: "Implemented"
---

# Plan - Feature 048

## 1. 目标

在不改动 backend canonical contract 的前提下，重做普通用户主路径的页面表达，让 `Home / Settings / Chat` 先回答：

1. 当前发生了什么
2. 这对我有什么影响
3. 我下一步该做什么
4. 系统等待时正在做什么

## 2. 非目标

- 不重做 front-end data layer 或 query registry
- 不修改 Butler / Worker 的核心 runtime 语义
- 不把 `Advanced` 去掉或降级成普通页面
- 不通过新增 case-by-case backend 特判来制造前端“进度”

## 3. 设计原则

### 3.1 单一主行动优先

每个普通页面首屏都必须存在一个最重要动作，而不是多个状态并列争夺注意力。

### 3.2 用户语言优先

普通页面展示“影响 + 下一步”，不要展示内部状态名、raw diagnostics、runtime IDs。

### 3.3 技术事实不消失，只后移

raw A2A、tool traces、diagnostics 保留在 `Advanced` 或折叠层；普通页面只显示解释后的摘要。

### 3.4 等待也算产品行为

等待态必须被设计成正式状态，不再视为“空白区 + 按钮 disabled”。

## 4. 参考证据

- OpenClaw onboarding/wizard 强调“推荐路径 + 最快首次聊天”：
  - `_references/opensource/openclaw/docs/start/wizard.md`
- OpenClaw dashboard/control-ui 强调用户入口与 admin surface 分层：
  - `_references/opensource/openclaw/docs/web/dashboard.md`
  - `_references/opensource/openclaw/docs/web/control-ui.md`
- 当前代码中需要整改的具体入口：
  - `octoagent/frontend/src/domains/home/HomePage.tsx`
  - `octoagent/frontend/src/domains/home/readiness.ts`
  - `octoagent/frontend/src/domains/settings/SettingsOverview.tsx`
  - `octoagent/frontend/src/pages/ChatWorkbench.tsx`
  - `octoagent/frontend/src/hooks/useChatStream.ts`

## 5. 实施切片

### Slice A - Home 首屏重构

- 重写首页 hero 的结论逻辑
- 重新定义卡片统计口径
- 去掉 raw summary / raw object 泄漏
- 让“待处理 / 当前工作 / 最近完成”具有明确点击落点

### Slice B - Settings 首屏最少必要路径

- 提炼首屏 checklist
- 将“连接真实模型 / 检查配置 / 保存配置 / 返回聊天验证”做成清晰顺序
- 降低全量配置结构的首屏权重

### Slice C - Chat 等待态与协作进度

- 增加发送后立即出现的占位反馈
- 增加基于 runtime truth 的折叠式协作进展
- 将失败解释重写为“信息不足 / 工具受限 / 当前降级”

### Slice D - 验收与文案基线

- 更新前端测试
- 增加普通用户语言的黄金路径断言
- 补充与 `Advanced` 的职责边界说明

## 6. 风险

- 如果只改文案不改信息结构，首页仍会让用户迷路
- 如果直接展示 raw A2A message，会从“没反馈”走向“过度技术化”
- 如果将等待态绑定到完整 A2A message 回流，短期会继续存在无反馈窗口

## 7. 验证方式

- Home 首屏快照与交互测试
- Settings 首屏 checklist 测试
- Chat 发送后等待态与协作折叠面板测试
- 手动走查：echo 模式、真实模型模式、degraded runtime、freshness delegation 四类场景
