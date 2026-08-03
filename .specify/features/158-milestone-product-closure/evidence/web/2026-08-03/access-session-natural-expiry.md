# F150 个人部署 Access 会话自然过期证据

## 结论

- 时间：2026-08-03 00:10（Asia/Shanghai）；
- hostname：个人部署 `octo.maojiwang.work`；
- 浏览器：用户现有 Chrome 会话与原有 OctoAgent 标签；
- 结果：PASS；
- 没有读取 Cookie、localStorage、Access JWT、密码或验证码；
- 没有代填邮箱、请求验证码或重新登录；
- 没有修改 Cloudflare、Gateway、tunnel、DNS 或浏览器存储。

## 可观察状态

同一用户标签在动作前仍显示：

```text
title=OctoAgent
host=octo.maojiwang.work
route=existing chat route
```

只执行一次普通页面 reload 后，Cloudflare Access 在进入 OctoAgent SPA、API 或 SSE 前
直接将同一标签送回身份边界：

```text
title=Sign in ・ Cloudflare Access
heading=Log in to OctoAgent Personal Web
control=Email / Send login code
```

原 OctoAgent chat route 仍作为登录完成后的 redirect target，由 Access 管理；报告不保存
该临时登录 URL、签名参数或其它会话材料。标签已停留在登录边界并交还用户。

## 判定

该状态转换来自真实经过时间后的既有登录会话，不是主动点击 logout、清理 Cookie、改短
fixture TTL 或注入 mock。它与此前已经通过的主动登出、重新认证、登录后 Settings ready、
真实对话/SSE、一次性 Gateway 502 恢复共同补齐 F150 个人部署生命周期。

F150 Feature 可以判定为 PASS；F158 整体仍因 F154-F156、mainline/CI 和最终物理重启
保持 `GATE_VERIFY=false`。
