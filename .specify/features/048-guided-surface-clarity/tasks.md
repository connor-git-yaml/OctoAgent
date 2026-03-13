# Tasks - Feature 048

## T001 - 重写 Home 首屏结论与主行动

- 基于 setup / diagnostics / operator pending / active work 综合推导首页主状态
- 删除直接使用 raw readiness label 作为 hero 标题的做法
- 为每种主状态提供单一主 CTA

## T002 - 重定义首页卡片语义与统计口径

- 区分 `待处理 / 正在进行 / 最近完成 / 历史累计`
- 修正 `当前工作 19 / 进行中 1` 这类混合表达
- 为卡片补点击落点或明确不可点击说明

## T003 - 清理首页普通视图的内部泄漏

- 修复 `telegram: [object Object]`
- 去掉 raw context summary/tool traces 在首页的直接展示
- 将内部 summary 转为普通用户说明或迁移到 `Advanced`

## T004 - 重做 Settings 首屏最少必要路径

- 提炼 checklist 与首屏主 CTA
- 首屏突出 echo/real model、最少缺失项和验证路径
- 降低 provider/memory/security 全量结构的首屏噪音

## T005 - 实现 Chat 等待态

- 发送后立即插入处理中的占位反馈
- 为 restoring / sending / delegated / synthesizing 等状态建立前端展示规则

## T006 - 实现折叠式“内部协作进度”

- 基于 `A2AConversation / A2AMessage / Work runtime_summary` 派生用户语言阶段
- 默认只显示高层进展
- 折叠层显示最近 2-3 条解释后的事件

## T007 - 统一失败解释

- 区分信息缺失、环境受限、系统降级三类失败
- 更新聊天与首页的用户提示

## T008 - 测试与手动验收

- 更新 `HomePage`、`SettingsPage`、`ChatWorkbench` 相关前端测试
- 手动验证首次上手、天气查询等待态、配置未完成、degraded runtime 四个路径
