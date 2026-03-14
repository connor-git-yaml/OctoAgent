# Research Synthesis - Feature 051

## 结论

这轮问题不是“文案还不够顺”，而是 `Home / Settings` 仍然沿用控制台信息架构。  
`048` 已经修过第一轮普通用户路径，但还没有把“状态卡片 + 控制台统计”改成真正的入口页。

## 本地代码证据

1. `Home` 仍然把 setup / operator / active work / memory / context 都堆在首页  
   - `octoagent/frontend/src/domains/home/HomePage.tsx`
   - 典型问题：
     - `pendingSummary` 用 `total_pending`，却只展示 approvals / pairing
     - `activeWorkSummary` 混合“历史累计”和“当前进行中”
     - “当前提醒”“背景记忆”仍占首页主位

2. `WorkbenchLayout` 仍然在全局 shell 里展示无解释的累计计数  
   - `octoagent/frontend/src/components/shell/WorkbenchLayout.tsx`
   - 典型问题：
     - 顶栏 chip：`待确认 / 可见 work / 记忆记录`
     - 侧栏卡片：`待你确认` 与 `记忆摘要 current records`
     - lastAction 直接拼接 `[code]`

3. `operator_summary.total_pending` 与前端文案不一致  
   - `octoagent/apps/gateway/src/octoagent/gateway/services/operator_inbox.py`
   - `total_pending = approvals + alerts + retryable_failures + pairing_requests`
   - 但首页和 shell 只解释 approvals / pairing，导致用户读不懂数字

4. `SettingsOverview` 仍然像配置中心首页  
   - `octoagent/frontend/src/domains/settings/SettingsOverview.tsx`
   - 典型问题：
     - 首屏仍然强调多个配置域
     - 六宫格摘要没有真正区分“必须做 / 以后再做”

## 本地参考仓库启发

### OpenClaw

参考：
- `_references/opensource/openclaw/README.md`

可借鉴点：
- 安装与首次使用强调单一推荐路径（wizard / onboard）
- 主入口先讲“现在怎么开始”，而不是先铺控制平面术语
- control plane 是存在的，但产品表达里不会让 dashboard 抢走用户第一次动作

### Agent Zero

参考：
- `_references/opensource/agent-zero/README.md`

可借鉴点：
- Quick Start 与首次工作 chat 路径非常明确
- 产品首页先说“这个系统能帮你做什么”，再引到配置与扩展
- 多 Agent、memory、projects 是能力，不是首页默认负担

## 设计判断

这轮应当把 `Home / Settings` 收成：

1. `Shell`
   - 只保留当前上下文与简洁状态
   - 去掉全局累计计数

2. `Home`
   - 一个主结论
   - 一个主动作
   - 一个影响说明
   - 一组真实事项或最近记录

3. `Settings`
   - 现在必须做的
   - 现在不用急的
   - 做完去哪里验证

## 在线调研

- 本 Feature 未新增在线调研点
- skip_reason: 当前问题已经由真实用户体验、现有代码与本地参考仓库足够支撑；不需要再补外部网页证据
