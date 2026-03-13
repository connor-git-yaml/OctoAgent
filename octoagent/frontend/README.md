# OctoAgent Frontend

OctoAgent Web UI 使用 `React + Vite + React Router`，当前按 `domains / platform / ui / styles` 分层维护，避免继续回到单页巨石。

## 目录职责

- `src/domains/`
  - 放页面域模块与局部 section、view-model、presentation helper。
  - 新功能优先落在具体 domain，下沉到共享层前先确认是否真是跨域复用。
- `src/platform/`
  - 放 shared query、action、contract manifest 与 canonical control-plane 对接逻辑。
  - snapshot / resource / action 的刷新策略只在这里维护，不在页面里重复拼装。
- `src/ui/`
  - 放跨域 UI primitives，例如 `PageIntro`、`StatusBadge`、`InlineCallout`、`ActionBar`。
  - 这里只承载稳定 pattern，不绑定具体资源类型。
- `src/styles/`
  - 放 token、shell、shared UI pattern 样式。
  - `index.css` 保留页面级与历史样式；新的共享样式优先进入 `styles/`。
- `src/pages/`
  - 主要作为路由入口与兼容层。页面逻辑一旦持续增长，应优先迁入对应 `domains/*`。

## 当前规则

- canonical control-plane contract 以 `src/platform/contracts/controlPlane.ts` 为集中来源。
- shared refresh / invalidation 逻辑以 `src/platform/queries/controlPlaneResources.ts` 和 `src/platform/actions/controlPlaneActions.ts` 为单一事实源。
- 日常路径默认是 `Home / Chat / Agents / Work / Memory / Settings`，`Advanced` 只承载高级诊断。
- 普通用户主路径避免直接暴露内部 ID、A2A、runtime truth、raw schema 等开发术语；如确需保留，放到 `HoverReveal`、诊断区或 `Advanced`。

## 本地验证

在 `octoagent/frontend` 目录执行：

```bash
./node_modules/.bin/tsc -b
./node_modules/.bin/vitest run src/App.test.tsx src/pages/ControlPlane.test.tsx
npm run check:complexity
```

若修改了 shared contract / refresh 策略，额外跑：

```bash
./node_modules/.bin/vitest run \
  src/platform/contracts/controlPlaneContract.test.ts \
  src/platform/queries/workbenchRefresh.test.ts \
  src/platform/actions/controlPlaneMutation.test.ts
```
