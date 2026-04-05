# Tasks - Feature 051 Home / Settings IA Reset

## Phase 1 - Shell 降噪（P1）

- [x] T001 重构 `WorkbenchLayout` 顶栏与侧栏，移除 `待确认 / 可见 work / 记忆记录 / current records` 等无意义累计计数
- [x] T002 将全局 action feedback banner 改为纯用户语言，去掉 `[CODE]` 泄漏
- [x] T003 为 shell 增加一个简洁的“当前状态”摘要，而不是并列数字

## Phase 2 - Home 主叙事重做（P1）

- [x] T004 重写 `readiness` / 首页主叙事逻辑，移除“先做一次启动检查”这类 setup ceremony 主结论
- [x] T005 基于 `operator_items`、`active works`、`degraded runtime` 重构首页主面板
- [x] T006 删除首页中“背景记忆”“当前提醒”“历史累计 work”这类普通用户无价值区块
- [x] T007 保留最近一次记录，但清理 raw summary / tool 痕迹泄漏

## Phase 3 - Project / Workspace 语义收口（P1）

- [x] T008 只有在存在多个可选 project/workspace 时才显示切换器
- [x] T009 将切换器说明改为“切换工作上下文”，并避免当前无选项时空转展示

## Phase 4 - Settings 首屏重做（P1）

- [x] T010 重构 `SettingsOverview` hero，只保留最少必要路径和验证出口
- [x] T011 移除首屏六宫格配置中心式摘要，改为“现在先做 / 现在不用急 / 配完去哪验证”
- [x] T012 用 project/workspace 名称而不是 raw id 表达当前上下文

## Phase 5 - Verification

- [x] T013 更新 `HomePage.test.tsx` 覆盖 ready / pending / degraded / multi-workspace 场景
- [x] T014 新增或更新 `WorkbenchLayout` 测试，验证 shell 不再泄漏 code 和累计计数
- [x] T015 更新 `SettingsPage.test.tsx`，验证首屏主叙事和最少必要路径
- [x] T016 运行前端定向测试与 `npm run build`
