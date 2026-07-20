# F150 Cloudflare Tunnel 远程访问 — Implementation Plan

> 目标：以 Cloudflare named tunnel + Access 提供唯一的浏览器远程入口；本计划不建立多 provider/profile 抽象，也不复制 Cloudflare 已提供的浏览器 session。
>
> 实施前置：F151 的依赖方向、clean-wheel 启动、runtime truth 与 fail-closed 门禁已通过。Phase 0 只读 spike 可提前并行，用于尽早退休可行性风险。

## Phase 0：契约与真实链路 spike

- 用最小 FastAPI SSE endpoint 通过真实 named tunnel 验证首事件、事件间延迟、断线和重连。
- 固定 Access JWT 的 issuer、audience、JWKS 与 identity claim 契约。
- 记录正式 tunnel/service 的最小配置，禁止 quick tunnel 与前台常驻进程。

**Gate**：SSE 达标且安全契约可执行；否则暂停 F150，另立协议修复 Feature。

## Phase 1：配置与 front-door 安全模型

- 在现有 front-door 配置中增加 `cloudflared` mode 及其最小字段。
- 扩展 host×mode 暴露矩阵：只允许 loopback 回源；外部网卡绑定 fail closed。
- 增加 Access JWT verifier：JWKS 缓存、签名、issuer、audience、expiry、identity allowlist。
- 将 JWT 验证接入现有 Guard、失败限流与脱敏链，不另建平行认证中间件。

**Gate**：header 伪造、错 audience、过期/未知签名、JWKS 故障矩阵全部通过。

## Phase 2：浏览器认证边界

- 将已验证 Access identity 映射为唯一 owner，不新增配对码、remote session 或 browser device 表。
- HTTP 与 SSE 复用同一 JWT/identity 校验；mutation 额外执行 Host/Origin/CSRF 策略。
- 明确本机直连与 tunnel 回源的判定：无 proxy marker 的 loopback 请求保持本机能力；任何带 proxy marker 的请求都必须通过 Access JWT，不能因 TCP 来源是 `127.0.0.1` 而被信任。
- Access session 到期、登出、撤销与 JWKS 故障全部 fail closed；浏览器不接收 bearer/service token。

**Gate**：owner allowlist、伪造 proxy header、CSRF/Origin、Access 过期/登出、刷新恢复与日志 secret 扫描全绿；数据库 schema 无远程浏览器会话新增。

## Phase 3：管理界面

- 实现单一 Cloudflare 远程访问设置页，不出现方案选择器。
- 展示配置状态、域名、最近验证与普通用户可理解的错误恢复。
- 展示 owner identity、打开远程站点、Access 登出与会话恢复入口；删除配对码、设备列表和二次登录页需求。
- 技术诊断收进 Advanced，不向主流程暴露 JWT/header/service 配置细节。

**Gate**：移动端 L1 覆盖未配置、Access 登录、过期/登出、刷新恢复、失败恢复与窄屏布局。

## Phase 4：部署与验证闭环

- 文档化官方 `cloudflared` service、named tunnel、DNS 与 Access application 配置。
- 在 `octo doctor` 增加 Cloudflare 配置与 origin 安全检查；不新增通用 remote 命令组。
- 建立真实 named tunnel live 验证，覆盖 SPA、REST、SSE 与 secret 扫描。
- 更新 Blueprint、代码架构导览和 Milestone 状态。

**Gate**：本地回归、L1、确定性 e2e、真实 tunnel live 验证均通过，才可标记 F150 完成。

## 文件落点原则

- Gateway：复用 `frontdoor_auth.py` / `frontdoor_exposure.py`，只添加 Cloudflare 所需验证器与配置。
- Gateway CLI/DX：在 F151 迁移后的 `apps/gateway/cli` 复用 `doctor.py`，不把产品运维逻辑重新塞回 Provider，也不恢复已删除的通用远程编排层。
- 数据：F150 不新增数据库表或独立状态文件；Cloudflare configuration 只落既有配置面，secret 不归 Octo 保存。
- Frontend：复用 F148 组件与设置页信息架构，不创建 provider selector。
- 测试：L4 安全矩阵 + L3 全链 + L1 手机流程 + live named tunnel 四层闭环。

## 实施顺序

`真实 SSE spike → F151 gate → JWT/暴露面/浏览器边界 → UI → 部署/live 验收 → 文档同步`
