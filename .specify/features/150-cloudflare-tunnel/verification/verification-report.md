# Verification Report：F150 Cloudflare Tunnel 远程访问

**Feature ID**：`150`
**复核日期**：2026-07-28
**复核分支**：`codex/f158-milestone-product-closure`
**基线提交**：`db3214fff722c6f969baf99528a76fc03a1e21a1`
**状态**：本地产品闭环通过；个人部署 connector 已恢复，等待真实登录态复验、F158
提交与权威 CI

## 复核背景

F150 原任务与 live attestation 已证明 Gateway、Cloudflare Access、named tunnel、
SSE 和桌面 Web 认证链成立，但主线中的 `RemoteAccessSettings` 仍是没有生产
consumer 的 standalone component。F158 将该现状重新判为“后端能力已交付、用户入口
未完成”，并补齐 Settings composition、视觉样式、浏览器旅程与本报告。

本报告不会把手机浏览器计为产品交付。桌面保留 Web；手机产品只走后续原生 iOS App。

## 本轮验证范围

- Settings 生产 composition 中真实渲染 F150 remote-access 区块；
- `RemoteAccessSettings` 继续复用现有 API adapter 与 view-model，不创建第二
  transport、认证状态机或移动 Web 入口；
- unconfigured 状态、打开桌面 Web、Access logout/recovery 与 Advanced 诊断；
- Cloudflare Access 登录回跳、刷新、真实 Gateway SSE、过期、重新认证、登出与
  一次性上游错误恢复；
- 390px 只验证桌面 Web 的窄窗口 overflow 与键盘焦点；
- 早期 Claude Design 的深色层级、绿色强调和紧凑 Settings 卡片；
- 全前端单元回归、production build、复杂度门和全 Playwright 回归。

## 执行结果

### 单元与组件

```bash
cd octoagent/frontend
npm test -- --run
```

结果：`70 files / 598 tests passed / 0 failed`。

其中新增组合层合同：

- `src/domains/settings/SettingsCenter.remote-access.test.tsx`
- `src/domains/settings/RemoteAccessSettings.test.tsx`
- `src/api/remote-access.test.ts`

均通过。组合层合同直接断言 Settings 页面出现“从电脑安全访问 Octo”，避免再次把
standalone component 当成用户可达交付。

### 构建与复杂度

```bash
cd octoagent/frontend
npm run build
npm run check:complexity
```

结果：均通过。production build 转换 `198 modules`；新增
`src/styles/claude-workbench.css` 为 `648/700` 行，所有既有前端复杂度上限保持通过。

### 浏览器 E2E

```bash
cd octoagent/frontend
npm run test:e2e
```

结果：`19 passed / 1 skipped / 0 failed / retries=0`。

F150 场景：

1. Access 登录回跳、刷新、真实 Gateway SSE、登出、过期后重新认证与错误恢复；
2. Settings 导航后真实出现 remote-access 状态、恢复动作与 Advanced 诊断；
3. 390px 仅作为 Web 窄窗口验证，不声明手机产品或 iOS 已交付。

### 真实浏览器截图

- 文件：
  `../158-milestone-product-closure/evidence/f150/2026-07-28/settings-remote-access-browser.jpg`
- SHA-256：
  `c9172f32e166bcbb0be22aaff74ba24b412f5c7d572327dc137748c211bcaac0`
- 尺寸：`1124×900`

截图来自真实 Gateway/Web 页面，不是静态 HTML 或设计稿。

## 历史 live 证据

- T003 named tunnel SSE：
  `evidence/live/T003-named-tunnel-sse/attestation.v1.json`
  - SHA-256：
    `5685d1d976bf522b54f78c25548355ce0b9dbbb4225920feccb868e9319893a9`
- T015 production Web：
  `evidence/live/T015-production-web/attestation.v1.json`
  - SHA-256：
    `e219136a51dd9f5855d7a77c8c96e33bc82817e270da8e49532f7312ffa9148d`

两份历史 attestation 继续作为个人部署的脱敏外部证据，本轮没有重放或修改
Cloudflare 账户、DNS、tunnel、Access application 或系统 service。

## 2026-07-28 个人部署 connector 恢复

本轮实际复验发现，个人部署的 Gateway loopback origin 返回 `200`，个人部署子域的
DNS 与 Cloudflare Access 登录重定向也都正常；但长时间运行的 `cloudflared`
connector 为 `0` 条 active connection。日志证明本机代理/TUN 曾把 Cloudflare edge
解析到 `198.18.0.0/15` Fake-IP 地址段，导致 TCP `7844` 持续超时。

系统 DNS 恢复返回真实 edge 地址后，重启同一个 LaunchAgent，connector 恢复为
`4` 条 active connection，origin proxy error 计数为 `0`。本轮没有修改 Cloudflare
账户、DNS、Access application、named tunnel 或凭证。完整脱敏记录见
[`connector-recovery-2026-07-28.md`](connector-recovery-2026-07-28.md)。

该结果只证明 origin、DNS、Access 边界和 connector 已恢复，不把未认证的 `302`
登录重定向冒充登录后产品通过。登录后的 SPA、API、SSE、刷新、过期、重新认证、登出
及 Settings 入口仍需真实浏览器认证态复验。

## 架构与安全复核

- production consumer 只有 Settings composition；
- remote-access adapter/view-model 仍为单一数据路径；
- 没有新增 browser bearer、Octo Web session、pairing/device 表或 service token；
- 没有新增手机浏览器、WebView 或 iOS 实现；
- Advanced 承载诊断信息，普通区域使用用户语言；
- Access 与 Gateway 的安全合同、T003/T015 attestation 字节未修改。

## 剩余交付门

本地行为与产品入口已经闭合，但 F150 的最终主线状态仍依赖 F158：

1. 将本轮修正提交到分支；
2. 权威 CI 在提交字节上通过；
3. 在已恢复 connector 的个人部署域名完成登录后 SPA、API、SSE 与 Settings 入口复验；
4. 合并主线后校正 Blueprint/Milestone completion audit。

在这四项完成前，本报告不得被解释为 F158 整体 Goal 已完成。
