# F150 设计岔路（必须回用户拍板，带推荐 + 论证）

> 用户拍板后据结论收敛 spec/plan 再实施。安全核心 = 岔路①（JWT 纵深验证）+ 岔路②（配对码语义）。

---

## 岔路① gateway 要不要代码层校验 Cf-Access-Jwt-Assertion

**问题（产品视角）**：手机经 Cloudflare Access 登录后，请求到你家 Mac 上的 Octo 时，Octo 要不要**自己再验一遍**"这请求真是经过我的 Access 认证来的"，还是**信任 Cloudflare 边缘已经挡好了**？

**选项**：
- **A. 验（gateway 代码层校验 JWT）**：Octo 拉 Cloudflare 公钥，验 `Cf-Access-Jwt-Assertion` 签名 + iss + aud + 过期。无有效 JWT 一律拒。
- **B. 不验，纯信任边缘**：假设"能到 Octo 的流量都过了 Access"，Octo 只验 bearer。
- **B+. 只靠 cloudflared 连接器自验**（`originRequest.access.required`）：不写 gateway 代码，用 cloudflared 配置在回源前验。

**推荐：A（验）+ 叠加 cloudflared 连接器自验覆盖 SPA `/`**。

**论证**：
1. **零信任本义 = 不盲信任何单点**。Octo 绑 loopback，能到它的路径只有：(a) 经 tunnel（Access 应挡）、(b) Mac 本地进程直连。若用户**误配**——tunnel route 建了但**忘挂 Access 应用**，或 Access policy 手滑设成 allow-all——请求会带**空 JWT** 直达 gateway。选 A（require 有效 JWT）**当场挡住这类误配**，这是单人自建最可能的失败模式。选 B 则完全暴露。
2. **成本极低**：JWKS 拉取 + 缓存（kid 索引，6 周轮换自动兼容）+ 每请求一次 RS256 验签。`cryptography` 已是 gateway 依赖。
3. **cloudflared 连接器自验（B+）是必要补充不是替代**：gateway guard 只管 owner-facing API，**管不到 SPA `/` 挂载**（F130 已知 limitation：`/` 绕过 front_door）。cloudflared `access.required` 让**所有路由含 `/`** 在回源前就要求 JWT——补上 SPA 这个洞。故 **A（gateway 验 API）+ 连接器自验（覆盖 `/`）** 双管，不是二选一。
4. **代价**：JWKS 网络依赖（拉取失败要软化——§0.6 懒加载/不挡启动）；team_name/aud_tags 要进 config。可接受。

**若选 B**：spec 删 FR-3 大半，只留 cloudflared 连接器自验 + bearer；风险 R（Access 误配即公网裸奔 SPA）需用户明确接受。

---

## 岔路② 配对码语义

**问题（产品视角）**：手机第一次连上 Octo，怎么建立信任？Octo 的 owner API 有个 bearer 密钥（43 个随机字符）。手机怎么拿到它——**在手机上手输 43 个字符**？还是 Mac 上显示个**短码**、手机输短码换密钥？还是**干脆不要 bearer**、Access 登录就够了？

**选项**：
- **2A. 手机直接输 bearer**：与现在 Tailscale 流程一样。Access 登录后，SPA 让用户粘贴/输 bearer，存浏览器。**零前端改动、零新后端**，今天就能跑。缺点：手机键盘输 43 字符一次（可复制粘贴缓解）。
- **2B. 短配对码换 bearer**：`octo remote cloudflare enable` 显示 8 位短码；手机 Access 登录后输短码 → 后端换发 bearer（存浏览器）。TTL 10 分钟 / 单用 / 一次一码 / 重放防护 / 交换端点 Access-gated + bearer-exempt。UX 好，但新增后端（交换端点 + 码存储）+ **SPA 输码屏（F148）**。
- **2C. 去 bearer，纯 Access**：cloudflared 模式下 Access JWT 校验即认证，**不要 bearer**。UX 最简（Access 登录就完事，无码无密钥）。但：①与"复用 bearer 作纵深"的拍板方向冲突；②SSE `?access_token=` 深度依赖 bearer（去掉要动前端 SSE 协议 = F148 地盘）；③丢一层纵深（Access/CF 账号失陷即无兜底）。

**推荐：2B（短码换 bearer）——backend + CLI 归 F150，SPA 输码屏归 F148**。理由：
1. 保留 bearer 纵深（拍板方向）+ 不动 SSE 前端协议（bearer 仍是 SSE 凭证）。
2. 手机 UX 明显优于 2A（短码 vs 43 字符）；配对码是设计稿 OCT42 的意图。
3. **F150/F148 边界清晰**：F150 做码生成（CLI）+ 交换端点（后端）；F148 做输码 SPA 屏。**若 F148 输码屏排期滞后，2A 是天然 fallback**（同一 bearer，SPA 直接输即可），不阻塞 F150 后端先落。

**配对码安全参数（2B 采纳时）**：TTL 10min / 单用原子消费 / 一次一码（新 enable 失效旧码）/ 交换端点必过 Access JWT（否则公网知码即可换 bearer）/ 内存态存储（重启失效，短 TTL 本就重发）/ 码与 bearer 零明文进日志。

**2C 的价值不可忽视**：若未来想要"Access 即一切"的极简形态，2C 更纯粹。但当前 SSE 前端耦合 bearer + 拍板要 bearer 纵深 → 不选。留档为 v0.2 方向（配合 F148 SSE 协议改造）。

---

## 岔路③ cloudflared 起法：全自动 vs 检测+指引

**问题（产品视角）**：`octo remote cloudflare enable` 是**一键全自动**（Octo 自己建 tunnel、配 DNS、建 Access 应用、跑起来），还是**检测 + 手把手指引**（Octo 检查你装没装/登没登录，剩下 CF 控制台的部分给你清晰步骤，你配好后 Octo 接管跑）？

**选项**：
- **3A. 全自动**：`octo` 用 CF API token 建 named tunnel + DNS route + Access 应用 + policy + 跑 `tunnel run`。
- **3B. 检测 + 指引**（仿 F130 tailscale_helper 三态）：`octo` 检测 cloudflared 装否/`cert.pem` 登录否/named tunnel 存在否，未就绪给可操作指引（含 CF dashboard 步骤），就绪则接管 `cloudflared tunnel run` + 切 mode + 发配对码。

**推荐：3B（检测 + 指引）**。理由：
1. **CF Access 应用创建 + policy + AUD tag 获取本质是 dashboard/API-token 工作，不可无痛全自动**。全自动要存 **CF API token**（触 Constitution #5 secret 分区 + 泄露面），且 Access 应用的 IdP/policy 配置（选 email OTP vs Google vs GitHub、加哪些人）是**用户安全决策**，不该 Octo 代拍。
2. **3B 复用已验证的 F130 tailscale_helper 三态范式**（`probe → 指引 / 接管`），代码结构、DI exec、零 sudo、优雅降级全部照搬，风险最小。
3. 用户只做**一次性** CF 侧配置（建域名 zone + tunnel + Access 应用，官方文档 15 分钟），之后 `octo remote cloudflare enable` 接管 `tunnel run` + 切模式 + 发码。
4. **3A 的诱惑**（真一键）不值当：单人自建、一次性配置，为省这一次手工引入 API token 存储 + 全自动出错难诊断，ROI 负。

**若选 3A**：spec 需加 CF API token 的 #5 分区存储 + 全套 tunnel/DNS/Access 编排 + 回滚——规模从 M 涨到 L+，且安全面变大。

---

## 岔路④ URL/域：用户手配 vs octo 代配 DNS

**问题（产品视角）**：手机访问的网址（如 `https://octo.你的域名.com/`）——这个域名和它到 tunnel 的 DNS 绑定，是**你在 Cloudflare 控制台配**，还是 **Octo 用 API 帮你配**？

**前提（硬约束，非选择，research §B.7 confirmed）**：named tunnel + Access **强制要求用户在 CF 账户有一个托管域名**（免费计划可）。`<UUID>.cfargotunnel.com` 只是内部 CNAME 目标，**不能直接当对外 URL**。**没有域名就用不了 cloudflared 路径**（此时 Tailscale 仍可用）。

**选项**（真正的子决策 = DNS route + Access 应用谁配）：
- **4A. 用户 dashboard 手配**：用户在 CF 控制台建 hostname（`cloudflared tunnel route dns` 或 dashboard Public hostname）+ Access 应用（选 hostname + IdP + policy），把 **hostname + team_name + AUD tag** 填进 `octo` config。`octo` 不碰 DNS/Access。
- **4B. octo 用 CF API token 代配 DNS route**：`octo` 帮建 CNAME（`cloudflared tunnel route dns`），Access 应用仍用户手配（Access 是安全决策不代拍）。

**推荐：4A（用户手配）**。理由：
1. **避存 CF API token**（Constitution #5）——代配 DNS 要 DNS Write 权限 token，存哪都是泄露面，为省一条 CNAME 不值。
2. **Access 应用无论如何要用户配**（IdP 选择 + 谁能进 = 安全决策），既然 dashboard 已经要进去配 Access，顺手配 hostname route 边际成本极低（同一页面）。4B 只省半步却引入 token。
3. `octo remote cloudflare status` 打印**预期 URL** `https://<配置的 hostname>/`（不断言 live，仿 F130 remote_status 的 serve URL 措辞），并给"确认 tunnel/Access 就绪"的诊断指引。
4. **与岔路③一致**：3B（检测+指引）不代配 CF 侧，4A 同源哲学。

**统一到岔路③④的推荐**：`octo` 做**检测 + 指引 + 接管 tunnel run + 切 mode + 发配对码**；CF 侧（域名 zone / tunnel route / Access 应用 / AUD tag）用户 dashboard 一次性配，把 hostname/team/aud 填进 config。这是最小 secret 面 + 最小编排 + 复用 F130 范式的组合。

---

## 岔路汇总表（回拍板用）

| 岔路 | 推荐 | 一句话理由 | 若翻推荐的代价 |
|------|------|-----------|----------------|
| ① JWT 纵深验证 | **验**（+cloudflared 连接器自验覆盖 SPA `/`）| 零信任不盲信边缘；挡"tunnel 无 Access"误配；成本低 | 不验则 Access 误配即公网裸奔 |
| ② 配对码语义 | **2B 短码换 bearer**（backend F150 / SPA屏 F148；2A 手输 bearer 作 fallback）| 保 bearer 纵深 + 不动 SSE 前端 + UX 好 | 2C 去 bearer 要动 SSE 前端(F148)+丢纵深 |
| ③ cloudflared 起法 | **检测+指引**（仿 tailscale_helper）| Access 配置不可全自动；复用 F130 范式 | 全自动要存 CF API token + 规模涨 L |
| ④ URL/域 | **用户 dashboard 手配**（前提：必须有 CF 域名）| 避存 CF API token；Access 本就要手配 | 代配 DNS 要 DNS Write token |
