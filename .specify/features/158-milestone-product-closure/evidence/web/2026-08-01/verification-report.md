# 2026-08-01 个人部署 Web 登录态与真实模型验收

## 结论

- Cloudflare Access 登录态：PASS；
- 真实 OpenAI Codex doctor：PASS，`provider=openai-codex`、`model=gpt-5.5`；
- 真实 Web 对话：PASS；
- SSE 运行态与结束态：PASS；
- Task event chain：PASS，终态 `SUCCEEDED`；
- Chrome console warning/error：0；
- Access 过期、登出、重新登录与一次性故障恢复：本轮未执行，继续 MISSING；
- F150/F158 整体结论：仍为 `PARTIAL`。

## 运行环境

- URL：`https://octo.maojiwang.work/`；
- 浏览器：用户现有 Chrome 登录态；
- 执行时间：2026-08-01 15:25-15:27（Asia/Shanghai）；
- 页面顶层状态：snapshot=`ready`；
- diagnostics：`degraded`，原因=`recovery`,`memory`；
- UI 普通语言：`受限运行`、`可以继续用，但外部能力受影响`。

页面没有把 degraded 冒充健康，也没有把内部 reason code 直接暴露给普通用户。

## 真实对话与 SSE

输入：

```text
这是 F158 Web 真实链路验收。请只回复：F158_WEB_E2E_OK
```

可观察状态链：

```text
就绪 → 进行中 → 已完成 → 就绪
```

执行期间右栏显示同一会话的运行进度与最近动作；输入区切换为“加入队列”并在终态恢复
“发送”。模型实际回复：

```text
F158_WEB_E2E_OK
```

Task ID：`01KYY3E1Q3GVEQYPB2HRCE3F88`。Task Detail 显示：

- 2026-08-01 15:25:50 创建；
- 耗时 8 秒；
- Orchestrator 步骤成功；
- event `任务完成`；
- 状态变更终态 `SUCCEEDED`。

Chrome 两个页面的 console warning/error 均为 0。

## Settings 远程访问投影

真实登录态下打开 Settings，并进入“从电脑安全访问 Octo”区块。页面显示：

- “正在确认远程访问”；
- 脱敏 Web 地址 `o***.maojiwang.work`；
- 脱敏 owner `c***@gmail.com`；
- “打开电脑网页”“退出远程登录”“重新检查”与“高级诊断”。

本轮点击一次“重新检查”后，区块仍保持“正在确认远程访问”，没有把浏览器中已经
完成的 Access 登录和真实对话冒充为 Settings 已确认状态。页面 console
warning/error 为 0。该结果证明 Settings 入口、脱敏和 pending 投影存在，但也暴露出
当前投影尚未收敛为已确认；因此它是 T014 的真实未通过边界，不是 PASS 证据。

## 真实模型 Doctor

执行：

```bash
env LITELLM_LOCAL_MODEL_COST_MAP=True ~/.octoagent/bin/octo doctor --live
```

命令 exit=0；`model_live=PASS`，详情为真实模型调用成功：
`alias=main, provider=openai-codex, model=gpt-5.5`。总体为 `WARN` 的唯一原因是
`sleep_settings`，不是模型、认证、Gateway、tunnel 或 mobile manifest 失败。

## 视觉证据

| 文件 | SHA-256 | 尺寸 | 证明内容 |
|---|---|---:|---|
| `remote-workbench-ready-degraded.png` | `b26d22c0619ca32535f1e98863f3a2a6e6ed08b845b1386b2c578afcd1dbdac3` | 1084×830 | 登录后三栏工作台、诚实 degraded 普通语言与 Claude 早期视觉层级 |
| `remote-workbench-real-conversation-complete.png` | `76495ded2104467aae7bc2ddaef0f33a633d53983bb5a7f3fa26f9e2d4323df3` | 1084×830 | 真实输入、精确模型回复、已完成与右栏就绪 |
| `remote-task-event-chain-succeeded.png` | `821750c5e7defb1093dba4b17f9f9e85d8c23b03bd8334709b9414b536adfbae` | 1084×830 | Task Detail、Orchestrator 成功与 `SUCCEEDED` 终态 |
| `remote-settings-pending-verification.png` | `4b83bb96b5da037a997210f95ffa43213de61ba0506fa0ccf1ab23e896c12b32` | 1084×830 | 真实 Settings 远程访问区块、脱敏地址/owner 与点击“重新检查”后仍 pending 的诚实状态 |

截图来自当前个人部署和真实登录态，不是 mock、静态 HTML、设计稿或本地 fixture。

## 未通过边界

本轮成功旅程不能替代以下独立行为：

1. Access 会话过期；
2. 用户主动登出；
3. 重新登录；
4. 一次性上游错误后的恢复；
5. Settings remote-access 区块在上述状态间的同步；当前点击“重新检查”后仍为
   “正在确认远程访问”。

因此 F158 T045 可以完成，但 T014 继续保持 unchecked。
