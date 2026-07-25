# F150 Clarifications

## 2026-07-23 Design Gate 收敛

### Q1：为什么不把所有 Cloudflare 字段直接塞进 root config？

F151 已把 `manifest_path` 与 `owner_email` 预留为唯一扩展落点。versioned 非敏感 manifest 可同时被 loader、doctor、guard 与 UI projection 复用，避免四处复制 hostname/audience/tunnel facts；它不保存 secret，也不是第二 runtime state。

### Q2：本机 loopback 与 cloudflared 都来自 `127.0.0.1`，如何区分？

只有 peer loopback 且所有 Cloudflare/proxy marker 缺失才是 direct local。出现任一 marker 就强制验证 Access JWT；marker 本身不可信，只能触发更严格路径。

### Q3：为什么不使用 Cloudflare Cookie？

Cookie 是 edge session，Cloudflare 不保证把它转发给 origin。Gateway 只验证官方送到 origin 的 `Cf-Access-Jwt-Assertion`，不复制 session。

### Q4：Access Cookie 会不会带来 CSRF？

会，因此远程 mutation 除 JWT 外必须同时通过 exact Host、exact Origin 与 JSON media type gate。F150 不再创建第二 Cookie 或 CSRF secret。

### Q5：F150 是否负责最终 Settings 视觉？

F150 负责安全 contract、API projection 和最小可用入口；F149 在 F150 stable 后实现最终视觉。Claude Design 最初方案是视觉与交互基线，现有 Web UI 不是基线：实现应适配设计稿，只有功能合同、可用性或无障碍要求确有冲突时才调整设计，且不得建立第二 backend 状态机。

### Q6：实现时能否自动创建 tunnel、DNS 或 Access application？

不能。它们影响用户 Cloudflare 账户与系统 service，必须另获单次明确授权。默认流程只生成模板和做只读诊断。

### Q7：真实 SSE gate 用什么阈值？

连续 5 次通过；首事件 ≤5 秒，三个 500ms 事件独立分帧且到达间隔 250ms～2 秒，断线后 ≤10 秒恢复。失败即暂停，不偷偷降级轮询。

### Q8：手机是否还能通过 Safari 使用 Web？

不作为产品入口。电脑保留 Web；手机产品只走原生 iOS App。390px 响应式 Web 可以作为布局健壮性回归保留，但不能写成手机交付、移动认证或 iOS 验收。

### Q9：“唯一远程方案”是否意味着 Web 与 iOS 共用一种认证？

不是。唯一的是 Cloudflare named tunnel 网络基础设施，不是客户端或 trust contract。电脑 Web 使用 Access browser session；iOS 在 F153 通过真机 spike 冻结设备身份和 edge transport，并必须支持短期凭证与单设备撤销。

### Q10：iOS 能否内置 Cloudflare service token？

不能。service token 是长期 client secret，进入 App 包后不可保密。F153 只能选择不内置静态 secret 的方案；在选择前 F150 不创建 mobile route 或 Access Bypass。
