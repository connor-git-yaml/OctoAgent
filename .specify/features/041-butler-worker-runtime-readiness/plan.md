# Implementation Plan: Feature 041 Butler / Worker Runtime Readiness + Ambient Context

## 目标

把 OctoAgent 当前“能力已经存在，但默认运行面还不够像真实长期助手”的缺口补齐，重点解决 Butler 对实时问题的反应方式、child worker 的 capability bootstrap 和 freshness query 的验收链。

## 设计原则

1. 不给主 Agent 直接加执行面
   - Butler 仍是 supervisor-only
   - 外部世界查询优先走受治理 delegation

2. 优先补“默认上下文”和“默认能力认知”
   - 先让系统知道今天、现在、当前时区
   - 再让 Butler/Worker 明白自己有哪些可用执行面

3. 不新造 parallel backend
   - 继续复用 `AgentContextService`、`CapabilityPackService`、`worker.review/apply`、现有 built-in tools

4. 权限要可解释
   - `tool_profile` 和 capability summary 必须进入 runtime truth
   - network/browser 不是隐形魔法

## 实施阶段

### Phase 1: Ambient Runtime Context

- 在 `AgentContextService` 增加 ambient runtime block
- 定义当前时间来源、timezone/locale 解析顺序和 degraded 行为
- 视需要补一个 `runtime.now` built-in tool

### Phase 2: Butler / Worker Bootstrap Readiness

- 扩展 `bootstrap:shared`，加入 owner/project/runtime ambient facts
- 扩展 `bootstrap:general`，明确 freshness/external-fact query 的 delegation 策略
- 为 research / ops / dev bootstrap 增加 capability summary 和执行边界说明

### Phase 3: Worker Planning & Entitlement

- 强化 `workers.review` 对 weather/latest/web lookup objective 的识别
- 收口 research / ops worker 的 tool profile 策略
- 保证 `worker.apply` 派生的 child task/work 把 effective tool_profile 和 lineage 带全

### Phase 4: Acceptance & Runtime Truth

- 增加 freshness query acceptance matrix
- 覆盖：
  - 今天日期/星期
  - 天气（有城市 / 缺城市）
  - 官网 / 最新文档 / 网页查证
  - web/browser backend unavailable
- 在 control plane / workbench 显式展示相关 degraded reason 与 runtime truth

## 验收路径

1. 用户直接问“今天几号 / 周几”
2. Butler 基于 ambient runtime context 直接回答
3. 用户问“北京今天会不会下雨”
4. Butler 识别为 freshness query，并走 governed worker/tool 路径
5. 用户问“查一下某项目官网/最新文档”
6. 系统创建或调用合适的 worker，留下 tool_profile/runtime truth
7. 若 web backend 不可用，系统解释当前限制与下一步，而不是假装自己没有这类能力
