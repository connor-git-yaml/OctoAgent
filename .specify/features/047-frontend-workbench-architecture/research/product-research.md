# 产品调研：Frontend Workbench Architecture Renewal

**特性分支**: `codex/047-frontend-workbench-architecture`  
**日期**: 2026-03-13  
**范围**: OctoAgent 前端长期主义信息架构、日常工作台体验、调试面分层、可持续设计系统  
**输入**: 当前仓库 `frontend/` 代码、`docs/blueprint.md`、上一轮对 OpenClaw / Agent Zero / OpenHands / Open WebUI / LibreChat 的公开调研

## 1. 调研目标

围绕以下产品问题形成可执行结论：

1. 日常工作台与深度诊断是否应继续混在同一个前端心智里
2. 当前 `Home / Chat / Agents / Work / Memory / Settings / Advanced` 的导航分层是否足够稳定
3. 大量说明文案、内部术语和调试信息是否应继续暴露在主路径
4. 哪些同类产品交互模式值得借鉴，哪些实现方式不适合作为长期主义基线

## 2. 外部样本观察

### OpenClaw

- 优势不是单一 Web UI，而是把“平台能力、移动端 surface、插件 SDK、设计 token”当成一个长期系统来设计。
- 这类产品的关键不是页面多，而是 surface 间职责清晰：用户主路径、设备能力、平台配置彼此不混叠。
- 值得借鉴：
  - 明确区分 surface 与 core
  - 对代码体量和模块边界有强约束
  - UI 体现“可信、克制、可操作”，而不是营销式 AI 仪表盘

### Agent Zero

- 长处在于“过程透明”和“用户可介入”，用户几乎随时能看到 agent 在做什么。
- 弱点在于 Web UI 更像一层操作面板而不是长期维护的产品前端；信息量大、调试痕迹重、结构随功能增长容易失控。
- 值得借鉴：
  - 运行中过程的可见性
  - 关键动作随时可打断、可解释
- 不应照搬：
  - 调试信息直接作为主界面信息架构
  - 实现层和产品层边界混杂

### OpenHands

- 代表的是较成熟的现代 React 业务前端：域拆分更完整，数据层与 UI 层职责更清楚。
- 产品上强调“任务主路径”，把复杂配置与高级功能收拢到稳定导航和次级入口中。
- 值得借鉴：
  - 路由、状态、测试和 mock 体系成套存在
  - 首页与工作界面围绕“当前任务/下一步动作”组织

### Open WebUI

- 功能极广、页面众多，但能维持一定可用性，关键原因是按域组织 API / components / routes / stores。
- 风险也很明显：如果没有更强的 IA 纪律，功能会继续膨胀成巨型设置与巨型聊天系统。
- 值得借鉴：
  - 按功能域切分
  - Settings/Chat/Admin 各自形成独立系统

### LibreChat

- 富客户端能力强，交互覆盖面大，MCP/模型/聊天相关交互成熟。
- 但状态体系与 context 数量很多，复杂度高，长期维护成本也高。
- 值得借鉴：
  - 复杂聊天能力下的组件分层
  - a11y、主题、Toast、Query 这类基础设施先行
- 不应照搬：
  - 多套状态管理长期并存
  - 组件能力持续堆叠但没有统一约束

## 3. 用户路径结论

### 主路径应该是什么

OctoAgent 的主路径不是“把所有能力都放到首页”，而是：

1. `Home`：当前 readiness、当前 project、最重要下一步
2. `Chat / Work`：任务发起、运行中工作、审批反馈
3. `Agents`：能力编排、授权边界、默认执行主体
4. `Settings`：平台连接与 provider/catalog 配置
5. `Advanced`：只给调试、审计、深入排障使用

### 当前问题

- `Advanced` 仍然有一套几乎独立的控制台逻辑，和主工作台形成双轨。
- `Settings`、`Agents`、`ControlPlane` 曾经都在解释“系统是什么”，导致主路径不够稳定。
- 多个页面仍然通过大段说明文案解释产品，而不是通过布局、状态和 action hierarchy 让用户自然理解。

## 4. 设计原则建议

### 原则 A：主路径与调试路径必须彻底分层

- 日常用户不应该在第一层看到 raw snapshot、debug hints、内部命名和治理术语。
- `Advanced` 必须存在，但只能作为第二层诊断视图。

### 原则 B：页面优先表达状态与动作，而不是解释

- 首页首先回答“是否可用、现在发生什么、下一步去哪里”。
- Agents 首先回答“谁在工作、谁有权限、为什么此刻不能做某事”。
- Settings 首先回答“平台连接是否就绪、哪些 provider 已接入、还缺什么”。

### 原则 C：同类对象必须有稳定的目录视图

- Providers、Agents、Works、Memories 都应先有目录或列表，再有 detail / inspector。
- 避免把“配置表单 + 运行态 + 教学文案”三者塞进同一屏主结构。

### 原则 D：长说明应降级为帮助信息

- 主页面不再承载大量策略说明、迁移提示、内部 debug 语句。
- 说明类内容放入折叠 help、empty state guidance 或文档链接。

### 原则 E：视觉风格应服务“可信工作台”

- 方向应是克制、稳重、可扫描，而非通用 AI SaaS 的发光渐变风格。
- badge、key-value、timeline、inspector、matrix 比大段 copy 更重要。

## 5. 机会排序

### P1 机会

- 稳定主导航与 surface 边界
- 移除 `Advanced` 的独立数据编排
- 建立统一的 design token + primitive 层
- 让 `Agents / Settings / Work / Memory` 的视觉结构更一致

### P2 机会

- 将页面中的大表单拆为独立 section / drawer / editor
- 减少重复引导文案
- 强化 empty states、warnings、blocking actions 的一致表达

### P3 机会

- 更细的动画系统
- 可选主题个性化
- 更丰富的数据可视化

## 6. 结论

从产品和 UX 视角看，OctoAgent 现在最需要的不是“再加几个漂亮页面”，而是完成一次工作台层级重整：

- 主工作台围绕日常任务与状态组织
- `Advanced` 收敛为诊断面
- 页面从单体实现转向域化结构
- 视觉语言从“解释系统”转向“让系统自己可读”

这是一项适合作为独立 Feature 的长期主义改造。
