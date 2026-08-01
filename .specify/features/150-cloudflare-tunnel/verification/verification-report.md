# Verification Report：F150 Cloudflare Tunnel 远程访问

**Feature ID**：`150`
**复核日期**：2026-08-01
**复核分支**：`codex/f158-milestone-product-closure`
**初始基线提交**：`db3214fff722c6f969baf99528a76fc03a1e21a1`
**本轮复验基线提交**：`dc8b1b417fa0cb79a90c1aa290a2dc44e11fcad4`
**当前通过完整 CI 的代码/架构提交**：
`a2dca2badba40f87cec922946d65d21396e1b709`（run `30604533484`）
**当前个人部署提交**：`013762dfff200f3a1c1fc010fb59e1e4ffd52e4f`
**状态**：`PARTIAL`。F150 既有产品字节、本地合同与上一权威 CI 通过；当前状态修复
`013762df` 已通过 `98 passed`、静态门、F151 repository architecture gate 和个人部署，
但该提交的权威 CI 尚待完成；
connector、Access 登录、真实 OpenAI 对话、SSE 运行态与 Task 终态已经复验；主动
登出、重新认证挑战、用户完成重新登录及登录后 Settings 同步均已在 2026-08-01
后续复验中通过；会话自然过期与一次性错误恢复仍缺。

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

结果：均通过。production build 转换 `199 modules`；
`src/styles/claude-workbench.css` 为 `657/700` 行，所有既有前端复杂度上限保持通过。

### 浏览器 E2E

```bash
cd octoagent/frontend
npm run test:e2e
```

结果：F158 最终完整 Web suite 为 `39 passed / 0 failed / retries=0`；提交
`e84ffd435f742ba2784b85c074346ab63ecedbc1` 的 GitHub Actions
`l1-playwright` job 同样通过。

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

## 2026-07-28 当前分支个人部署

仓库正式 `repo-scripts/install-octo-user.sh` 路径已把
`~/.octoagent/app` 更新为提交
`e84ffd435f742ba2784b85c074346ab63ecedbc1`，完成 `uv sync`、前端依赖安装和
production build；managed checkout clean。重启 `com.octoagent.gateway` 后：

- loopback `/ready?profile=core`：`200`；
- loopback `/`：`200`；
- `octo.maojiwang.work`：`302` 到 Cloudflare Access 登录页；
- named tunnel LaunchAgent：running。

源码视觉样式 SHA 与部署 checkout 均为
`c550467990e6cf04f9822d15da142a3758b422aea464fb2d2ab0f5612618a325`。
Chrome 能枚举已有 Access 登录页，但接管该页超时，因此没有把登录页存在当作登录后
产品证据，也没有读取 cookie/local storage 绕过认证。

Gateway 启动日志还证明个人实例的 OpenAI Codex refresh token 已复用并返回 401。
这不是 Cloudflare Access 或 tunnel 的 502，但会阻断真实模型对话；需要重新登录
provider 后另行复验。

## 2026-07-31 当前个人部署复验

本轮重新从磁盘、进程与 HTTP 事实复算，未修改 Cloudflare、LaunchAgent、DNS、
Gateway 配置或用户凭证；在权威 CI 通过后，另以正式 installer 更新应用字节并执行
一次普通 Gateway 重启：

- managed checkout 为 `dc8b1b417fa0cb79a90c1aa290a2dc44e11fcad4` 且 worktree clean；
- `com.octoagent.gateway` 与 `work.maojiwang.octoagent-cloudflared` 均为 running；
- loopback `/ready?profile=core`、`/health` 与 `/` 均返回 `200`；
- `https://octo.maojiwang.work/` 仍返回 Access `302`；
- cloudflared ingress 仍只有 `octo.maojiwang.work`，没有 mobile hostname；
- loopback `GET /api/control/resources/remote-access` 返回
  `state=pending_verification`、`reason_code=REMOTE_ACCESS_VERIFICATION_PENDING`、
  `last_verified_at=null`，与 Settings 应展示的当前状态一致。

同日当前分支 Web 全量复验为 Vitest `70 files / 598 tests`、build 与 complexity
PASS、Playwright `39/39`、Claude 早期视觉基线 `14/14`，均未更新快照；证据见
`../158-milestone-product-closure/evidence/web/2026-07-31/verification-report.md`。
该部署提交的权威 GitHub Actions run `30602259193` 五个 job 全部通过；后续
Blueprint/Milestone active schema 真值提交
`a2dca2badba40f87cec922946d65d21396e1b709` 的 run `30604533484` 也由
backend-deterministic、frontend、architecture、benchmark 与 l1-playwright 五个
success job 闭合。两次 run 都不能把 Access `302` 或 `pending_verification` 提升为
登录态 PASS。

Chrome `connor` profile 的最新只读复核已能读取现有相关标签的标题和 URL：标签仍为
`Sign in ・ Cloudflare Access`，目标是 `octo.maojiwang.work` 的 Access 登录边界，
不是已认证 OctoAgent 产品页。目标标签已原样保留给用户；没有读取 Cookie/
local storage、代填账号或触发登录。下一次复验应由用户直接在该标签完成 Access
登录，再从同一 profile 验证 SPA/API/SSE/Settings、刷新、过期、重新认证和登出；
仍禁止读取 Cookie/local storage 绕过认证。

Provider 的正式恢复入口也已从当前部署 CLI 只读确认：
`~/.octoagent/bin/octo setup --provider openai-codex`。该流程会启动 PKCE 浏览器授权、
写入个人 credential/config，并在成功后自动执行 `octo doctor --live`；最终验收不得
传 `--skip-live-verify`。它会改变外部 OAuth 状态并需要用户完成账号确认，因此本轮
没有自行执行，也没有把命令存在当成真实模型可用。

## 2026-08-01 登录后真实产品旅程

用户已在现有 Chrome profile 完成 Cloudflare Access 登录。本轮复用该真实登录态，
没有读取 Cookie、local storage、密码或 Access JWT。当前个人部署工作台可观察事实：

- 顶层 snapshot=`ready`；
- diagnostics=`degraded`，原因精确为`recovery`与`memory`；
- 普通 UI 诚实显示“受限运行”“可以继续用，但外部能力受影响”；
- 页面为 Claude 早期视觉语言的三栏工作台，不是 Access HTML 或静态 mock。

随后发送不含敏感信息的真实验收消息：

```text
这是 F158 Web 真实链路验收。请只回复：F158_WEB_E2E_OK
```

页面经 SSE 从`就绪`进入`进行中`，输入区切换为“加入队列”，右栏展示当前步骤与
最近动作；约 8 秒后模型返回精确`F158_WEB_E2E_OK`，会话显示`已完成`并恢复`就绪`。
Task `01KYY3E1Q3GVEQYPB2HRCE3F88` 的详情页显示 Orchestrator 步骤成功、
`任务完成`事件与终态`SUCCEEDED`。工作台和任务详情页 console warning/error 均为0。

同日再次执行：

```bash
env LITELLM_LOCAL_MODEL_COST_MAP=True ~/.octoagent/bin/octo doctor --live
```

命令 exit=0，`model_live=PASS`，真实调用
`alias=main, provider=openai-codex, model=gpt-5.5`。总体`WARN`只来自
`sleep_settings`，不是认证、Gateway、tunnel 或模型失败。

完整事实、截图、尺寸与 SHA 见：
`../158-milestone-product-closure/evidence/web/2026-08-01/verification-report.md`。
同日后续从 Settings 执行主动登出，受保护根页重新进入 Cloudflare Access 登录边界；
owner 邮箱验证码请求成功并进入 10 分钟有效的 code challenge。验证码仍等待用户本人
输入，因此只把主动登出和重新认证挑战判为 PASS，不把重新登录完成、会话过期或
一次性故障恢复冒充已通过。对应截图与 SHA 继续见 F158 的同日验证报告。

用户随后亲自完成验证码。同一 Chrome 会话重新进入真实 Settings，证明重新登录
PASS；该页面同时暴露 status 永久停在 `pending_verification` 的单缺陷。针对该缺陷
建立 RED 后，只在既有 `remote_access_status` authority 内消费同一请求已经验证的
`CloudflarePrincipal`，没有增加持久认证状态机。组合回归 `98 passed`、F151
repository architecture gate PASS；提交 `013762df` 经正式 installer 部署并重启后，
真实 Settings 显示“远程访问已就绪”。两张部署前后截图及 SHA 见 F158 同日报告。

## 架构与安全复核

- production consumer 只有 Settings composition；
- remote-access adapter/view-model 仍为单一数据路径；
- 没有新增 browser bearer、Octo Web session、pairing/device 表或 service token；
- 没有新增手机浏览器、WebView 或 iOS 实现；
- Advanced 承载诊断信息，普通区域使用用户语言；
- Access 与 Gateway 的安全合同、T003/T015 attestation 字节未修改。

## 剩余交付门

本地行为、产品入口、登录后真实对话/SSE与模型终态已经闭合，但 F150 的最终主线状态
仍依赖 F158：

1. 完成 Access 会话自然过期与一次性错误恢复；主动登出、重新认证挑战、重新登录
   完成及 Settings 状态同步已经通过；
2. 合并主线后校正 Blueprint/Milestone completion audit。

在这些项目完成前，本报告不得被解释为 F158 整体 Goal 已完成。
