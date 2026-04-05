---
feature_id: "051"
title: "Home / Settings Information Architecture Reset"
created: "2026-03-14"
updated: "2026-03-14"
status: "Implemented"
---

# Plan - Feature 051

## 1. 目标

把 `Workbench shell + Home + Settings` 从“控制台拼盘”重构成真正的普通用户入口：

- Shell 不再展示无意义累计计数和内部 code
- Home 只回答“能不能用、现在先做什么、不做会怎样”
- Home 用真实事项而不是假数字
- Project / Workspace 只在真的可切换时出现
- Settings 首屏只强调最少必要路径

## 2. 非目标

- 不处理 Chat 协作速度、MCP 发现、Tool intent binding
- 不改 Advanced / Control Plane 的深度诊断页
- 不新增 Project / Workspace 创建流
- 不重做 Work / Memory 的独立页面

## 3. 设计原则

### 3.1 入口页不做控制台

普通用户主路径不再优先展示系统累计计数、内存记录数或后台状态码。  
看见一个数字，必须能回答“这对我现在有什么影响”。

### 3.2 一次只给一个主动作

首页不能同时推用户“检查设置 / 看待处理 / 看诊断 / 进聊天”。  
每次只给一个主动作，其余动作退成辅助入口。

### 3.3 真事项优先于总数

当系统说“有 2 项待处理”时，必须同时告诉用户这两项是什么。  
没有真实事项就不要显示数字。

### 3.4 配置页先帮用户开始用起来

Settings 首页先帮助用户完成第一次真实对话。  
完整配置结构属于第二层，不应继续霸占首屏。

### 3.5 只有真的可用，才展示切换入口

Project / Workspace 切换器不是首页装饰品。  
只有在存在多个有效选项时才展示，否则隐藏。

## 4. 本地参考证据

- `octoagent/frontend/src/components/shell/WorkbenchLayout.tsx`
- `octoagent/frontend/src/domains/home/HomePage.tsx`
- `octoagent/frontend/src/domains/home/readiness.ts`
- `octoagent/frontend/src/domains/settings/SettingsOverview.tsx`
- `octoagent/apps/gateway/src/octoagent/gateway/services/operator_inbox.py`
- `_references/opensource/openclaw/README.md`
- `_references/opensource/agent-zero/README.md`

## 5. 实施切片

### Slice A - Shell 降噪

- 去掉顶栏与侧栏里的累计计数
- 把 lastAction banner 改为纯用户语言
- 保留当前 project 信息，但不再在全局显示无解释数字

### Slice B - Home 主叙事重做

- 重写 readiness / primary narrative 逻辑
- 基于 operator items / active works / runtime degraded 生成真正的首页主结论
- 删除背景记忆、当前提醒、历史累计 work 这类块

### Slice C - 真实事项表达

- 用 `operator_items` 渲染首页事项列表
- 处理 approval / alert / retryable failure / pairing 的人话分类
- 改写“待确认”口径

### Slice D - Context 切换显隐

- 当前只有一个 project/workspace 时隐藏切换器
- 存在多个选项时再显示，并重写说明文案

### Slice E - Settings 首屏重做

- 去掉六宫格配置中心式首屏
- 改成“现在先做这几步 / 现在不用急着管 / 配完后去哪里验证”
- Project / Workspace 用名字表达，不再优先显示 raw id

### Slice F - 测试与回归

- HomePage 测试更新
- SettingsPage 测试更新
- 新增 WorkbenchLayout 测试
- 跑相关前端 build 与定向测试

## 6. 风险

- 如果只改文案、不改信息层级，页面仍会回到“看起来更友好，但还是没用”的状态。
- 如果继续依赖 `operator_summary.total_pending` 而不看 `operator_items`，待处理事项仍会误导。
- 如果 Settings 只是删卡片、不调整验证路径，用户仍然不知道“配完去哪试”。

## 7. 验证方式

- 首页可用性 smoke：ready / degraded / pending / echo 四条路径
- 首页切换器显隐测试
- Shell 去 code / 去累计计数测试
- Settings 首屏的 echo / ready 两条核心路径
- `npm test` + `npm run build`
