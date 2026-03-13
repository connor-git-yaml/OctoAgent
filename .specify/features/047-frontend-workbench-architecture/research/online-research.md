---
required: false
mode: full
points_count: 5
tools:
  - GitHub public repository metadata
  - raw GitHub source fetch
queries:
  - "OpenClaw package.json and repository tree"
  - "Agent Zero webui architecture and README"
  - "OpenHands frontend package.json and README"
  - "Open WebUI package.json and route/component organization"
  - "LibreChat client package.json and root app providers"
skip_reason: ""
---

# 在线调研证据：Frontend Workbench Architecture Renewal

## 1. 来源

1. OpenClaw repository + `package.json`
2. Agent Zero repository + `README.md` + `webui/index.js`
3. OpenHands `frontend/package.json` + `frontend/README.md`
4. Open WebUI repository tree + `package.json`
5. LibreChat repository tree + `client/package.json` + `client/src/App.jsx`

## 2. 关键发现

### OpenClaw

- 前端/客户端不是单一 Web app，而是多 surface 共享核心契约的系统化结构。
- 质量脚本中直接包含代码体量约束，说明其把“可维护性”当成正式工程目标。

### Agent Zero

- Web UI 以透明、可介入、实时为核心卖点。
- 实现方式偏模板化和原生 DOM/store，适合快速演进，不适合 OctoAgent 这类长期复杂 workbench 的架构基线。

### OpenHands

- 现代 React 路线成熟：Query、Router、i18n、MSW、测试体系是成套的。
- 对 OctoAgent 的最大启发不是视觉，而是前端基础设施完整度。

### Open WebUI

- 典型域化目录：`apis / components / stores / routes`。
- 说明高复杂度 AI 前端要长期活下去，必须按域组织而不是按单页组织。

### LibreChat

- 富能力聊天前端会迅速引入复杂的 provider、theme、query、drag-drop、routing、a11y 基础设施。
- 同时也展示了状态层并存的长期风险。

## 3. 对 OctoAgent 的影响

1. **不换栈，换组织纪律**
   - React/Vite/Router 继续保留
   - 强化 query 层、域模块与 contract sync

2. **不要复制 Agent Zero 的实现形态**
   - 学可观测与可介入
   - 不学“调试视图即主界面”

3. **工作台要比聊天更重要**
   - OctoAgent 是 Personal AI OS，不是单聊天产品
   - 因此导航、状态、能力边界、work/agent/provider 目录视图必须稳定

4. **必须把复杂度显式工程化**
   - 页面 LOC 约束
   - shared CSS 分层
   - shared data layer
   - golden-path 测试

## 4. 对设计的直接结论

- 主路径视觉风格应走“可信工作台”，而不是常见 AI 营销面
- 日常路径只显示任务、状态、能力和下一步
- 高级诊断单独保留，不再混入首页或设置主屏
- 列表 + detail + inspector 是比“长页面说明文 + 大表单”更适合 OctoAgent 的组织方式
