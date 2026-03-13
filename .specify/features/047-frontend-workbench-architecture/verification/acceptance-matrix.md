# Acceptance Matrix: Frontend Workbench Architecture Renewal

## A. 主路径 IA

| 场景 | 入口 | 期望 |
|------|------|------|
| 首页 readiness | `/` | 用户能直接看到 readiness、当前 project、下一步动作 |
| 日常路径无 debug 泄漏 | `/agents` `/settings` `/memory` | 首屏不出现 raw debug/内部治理术语主导内容 |
| 高级诊断隔离 | `/advanced` | 深度调试能力完整保留，但不回流污染主路径 |

## B. 统一数据层

| 场景 | 动作 | 期望 |
|------|------|------|
| action 触发资源刷新 | 任意 control-plane action | shared query/action 层统一失效并刷新 |
| Advanced 读取 snapshot | 打开 `/advanced` | 复用 shared workbench data layer |
| degraded 资源处理 | 后端返回 degraded | 页面保留 degraded 状态而非整页崩溃 |

## C. 页面模块化

| 场景 | 对象 | 期望 |
|------|------|------|
| AgentCenter 拆分 | `agents` domain | 主页面组合 section/pattern，不再单体膨胀 |
| SettingsCenter 拆分 | `settings` domain | provider/catalog/editor 逻辑独立模块化 |
| CSS 分层 | shared styles | token / primitive / shell / domain 边界清晰 |

## D. 契约与质量治理

| 场景 | 动作 | 期望 |
|------|------|------|
| contract sync | 后端资源模型变更 | 前端能集中更新类型来源 |
| 黄金路径回归 | 运行前端回归测试 | 首页、设置、Agent、聊天、work 核心路径覆盖 |
| 复杂度约束 | 新增大型页面逻辑 | 触发 LOC/complexity guard 或在 review 中可见 |

