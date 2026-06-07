# F123 出站 URL SSRF 预检 — 问题修复报告（Phase 1 诊断）

> Feature: F123 Outbound URL SSRF Guard
> 分支: `feature/123-outbound-ssrf-guard`（基于 origin/master @ 543a93b）
> 来源: 2026-06-08 竞品源码深读 workflow（对比 Hermes `tools/url_safety.py`），confirmed **HIGH** 真漏洞
> 范围: 仅出站 URL SSRF 预检；tool 结果**内容**扫描属 F108，不在本范围

## 问题描述

LLM 可被诱导让 OctoAgent 出站抓取**内网 / 云元数据端点**，造成 SSRF（Server-Side Request Forgery）：
- 直接传入 `http://169.254.169.254/latest/meta-data/iam/security-credentials/...` 偷取云实例 IAM 凭证；
- 传入一个公网 URL，但目标服务器返回 `302 Location: http://169.254.169.254/...`，httpx `follow_redirects=True` 自动跟进，绕进内网；
- 传入 `http://10.x / 127.0.0.1 / metadata.google.internal` 等扫描/访问内网服务。

此前未被利用的唯一原因是部署侧"碰巧没有可达的内部 http(s) 服务"——**一旦配置云部署（AWS/GCP/Azure）即可被直接利用窃取凭证**。属于 Constitution #5 Least Privilege / #7 User-in-Control 的硬缺口。

## 5-Why 根因追溯

| 层级 | 问题 | 发现 |
|------|------|------|
| Why 1 | 为何能抓内网/元数据地址？ | `web.fetch` / `browser.*` 工具把 LLM 传入的 `url` 直接交给 httpx 发请求 |
| Why 2 | 为何 URL 未被拦？ | 唯一校验 `_validate_remote_url`(capability_pack.py:1836) 只检 `scheme∈{http,https}` + `netloc` 非空，**无任何 IP/host 级拦截** |
| Why 3 | 为何只做 scheme 校验？ | 该函数初衷只为"拦非法 scheme"（如 `file://` / `javascript:`），从未把 SSRF 纳入威胁模型 |
| Why 4 | 为何威胁模型漏了 SSRF？ | F084 Harness 把入站威胁（ThreatScanner 扫 memory 写入）做全了，但**出站网络**只有 ThreatScanner 的窄管道，web/browser 出站请求是裸 httpx，无对应防线 [ROOT CAUSE REACHED at Why 4] |
| Why 5 | 为何未被现有机制捕获？ | 无针对出站 IP 的单测；`web.fetch` 标 `SideEffectLevel.NONE`（不过 ApprovalGate）→ 无人审；本地无内部服务 → 实测从未"打中"过 |

**Root Cause**: 出站 HTTP 工具（`web.fetch` / `browser.open` / `browser.navigate` / `browser.act`）的唯一 URL 校验 `_validate_remote_url` 仅做 scheme/netloc 检查，**缺少对解析后目标 IP 的私网/loopback/link-local/CGNAT/云元数据拦截**，且 `follow_redirects=True` 无逐跳重校验 → 公网 URL 可 302 绕进内网。

**Root Cause Chain**: LLM 传内网/元数据 url → `web.fetch`/`browser.*` 透传 → `_validate_remote_url` 只检 scheme → httpx 直发 + 自动跟 302 → 命中内网/元数据 → 凭证泄露。

## 影响范围扫描

### 攻击面（全部 LLM 可控 `url`，且全部汇聚到单一 chokepoint `_fetch_browser_page`）

| 工具 | SideEffectLevel | 调用链 | 是否 LLM 可控 host |
|------|-----------------|--------|--------------------|
| `web.fetch` | **NONE**（无审批） | network_tools.py:36 → `_pack_service._fetch_browser_page(url)` | ✅ 直接 |
| `browser.open` | REVERSIBLE | browser_tools.py:49 → `_browser_open_session` → `_fetch_browser_page(url)` | ✅ 直接 |
| `browser.navigate` | REVERSIBLE | browser_tools.py:98 → `_browser_open_session` → `_fetch_browser_page(url)` | ✅ 直接 |
| `browser.act`(click) | REVERSIBLE | browser_tools.py:144 → 跟进**页面解析出的 link** `target.url` → `_browser_open_session` → `_fetch_browser_page` | ✅ 间接（恶意页面可注入内网 link） |

**关键结论**：4 个入口全部经过 `CapabilityPackService._fetch_browser_page`（capability_pack.py:1855）→ `_validate_remote_url`。**只要在这个 chokepoint 加 SSRF 校验 + 在其 httpx client 加重定向逐跳重校验，即覆盖全部出站 web/browser 攻击面**。

### `web.search` / `_search_web`（capability_pack.py:1982）— 低风险，分级处理

- host **非 LLM 可控**：固定 `https://html.duckduckgo.com/html/` / `https://duckduckgo.com/html/`，LLM 只能控 `query`（走 `params`）。
- 但同样 `follow_redirects=True`（capability_pack.py:2014）。理论上被污染的 DDG 响应可 302 重定向出站请求。
- 搜索**结果** URL 只解析为 link 返回，不主动 fetch（除非 LLM 之后调 `browser.act`，那时已被 chokepoint 覆盖）。
- 处置：复用同一套"安全 httpx client"（含 redirect hook + 初始 URL 预检），保持单一硬化出站路径，defense-in-depth；不单独再写一条裸路径。

### 同步更新清单

- **调用方**：`_fetch_browser_page` / `_search_web` 改用安全 client；`_validate_remote_url` 升级或由新模块替代（删除死代码，不留旧逻辑）。
- **配置 schema**：`config_schema.py` 的 `OctoAgentConfig` 新增 `security: SecurityConfig`（`allow_private_urls: bool = False`），向后兼容（default_factory，存量 yaml 无该段→安全默认）。
- **测试**：新增 url_safety 单测（元数据/各私网段/loopback/CGNAT/IPv4-mapped IPv6/302→内网/开关行为/元数据即便开开关也拦）；既有 browser MockTransport 测试（用 `https://example.com`）需注入可控 DNS 解析以保持 hermetic（见下"测试 hermetic 风险"）。
- **文档**：blueprint `harness-and-context.md` + `milestones.md`（F123✅）+ security 章节同步（living-docs 漂移闸）。

## 测试 hermetic 风险（实施必读）

- 项目**无 pytest-socket / socket 拦截**（conftest + pyproject 已确认），真实 `getaddrinfo` 在测试中可用。
- 但 `test_capability_pack_tools.py` 的 browser e2e 用 `httpx.MockTransport` + `https://example.com`，**当前完全离线**。若 SSRF 校验对 host 做真实 `getaddrinfo("example.com")`，会给原本离线的测试引入网络依赖（违反 F087 hermetic 原则 + 离线 CI 会断）。
- **对策**：DNS 解析做成模块级可注入 seam（`url_safety._resolve_host`，默认 `socket.getaddrinfo`）。既有 browser 测试 monkeypatch 该 seam 返回公网 IP，保持离线确定性。

## 修复策略

### 方案 A（推荐）：新增 `harness/url_safety.py` 安全模块 + chokepoint 接入

参照 Hermes `url_safety.py`，按 OctoAgent 架构/规范（中文注释、env+yaml 双源开关、harness 安全层归属）重写：

1. **新模块 `apps/gateway/.../gateway/harness/url_safety.py`**（与 `threat_scanner.py` 同层，安全控制兄弟模块）：
   - `UnsafeUrlError(RuntimeError)` — 子类 RuntimeError，保持既有 tool-error 事件路径不变。
   - `ensure_url_safe(url) -> str`（sync，做阻塞 getaddrinfo）/ `async_ensure_url_safe(url)`（`asyncio.to_thread` 包装，async 热路径用，避免阻塞 event loop）。
   - 拦截：私网 RFC1918（10/8、172.16/12、192.168/16）、loopback（127/8、::1）、link-local（169.254/16、fe80::/10）、CGNAT（100.64/10，`is_private` **不覆盖**需显式）、reserved/multicast/unspecified。
   - **云元数据 always-block 地板**（即便 `allow_private_urls=true` 也拦）：169.254.169.254 / 169.254.170.2(ECS) / 169.254.169.253(Azure IMDS) / fd00:ec2::254 / 100.100.100.200(阿里云) + 整段 169.254.0.0/16 + IPv4-mapped IPv6 变体 + hostname `metadata.google.internal` / `metadata.goog`。
   - **IPv4-mapped IPv6**（`::ffff:x.x.x.x`）按嵌入 IPv4 判定（`ip.ipv4_mapped`）。
   - 开关 `_allow_private_urls()`：env `OCTOAGENT_ALLOW_PRIVATE_URLS`（true/1/yes 优先；false/0/no 显式关）→ yaml `security.allow_private_urls`（best-effort `load_config`，模块级缓存 + 测试 reset hook）→ 默认 **False**（fail-closed）。
   - DNS 解析 seam `_resolve_host`（可 monkeypatch）。fail-closed：解析失败/解析异常→拦截（默认安全分支；元数据 floor 对字面量不依赖 DNS）。
2. **`_fetch_browser_page` 接入**：`await async_ensure_url_safe(url)` 预检 → httpx client 加 `event_hooks={"request":[hook]}`，hook 对每个 request.url（含每跳 302 目标）`await async_ensure_url_safe(...)` → 不安全则 raise 中断重定向。删除旧 `_validate_remote_url`（死代码不留）。
3. **`_search_web` 接入**：同一安全 client（redirect hook + 初始 URL 预检），保持单一硬化出站路径。
4. **配置 schema**：`SecurityConfig{allow_private_urls: bool=False}` 挂到 `OctoAgentConfig.security`；`octoagent.yaml.example` 加注释示例。

**v0.1 边界 / 已知 limitation（spec 明示）**：
- **DNS rebinding (TOCTOU)**：预检 getaddrinfo 与 httpx 实连 getaddrinfo 之间，攻击者控的 TTL=0 DNS 可换 IP。彻底修需连接级校验（egress proxy / pinned-IP connector），超 v0.1 范围 → 标 limitation。v0.1 至少挡：字面量内网/元数据 IP + 重定向逐跳 + hostname 解析后首检。
- 出站 tool 结果**内容**扫描（web/MCP/terminal 输出裸进上下文）属 F108，不在本范围。

### 方案 B（备选，否决）：仅在 `web.fetch` 工具层加 if 判断拦 169.254.169.254

否决理由：①违反 Constitution #10 Policy-Driven（拦截散在工具层而非收敛单一入口）；②漏 browser.* / 重定向 / 私网段 / IPv6；③硬编码关键词不可维护。

## Spec 影响

- 无既有 spec 直接覆盖出站网络安全 → 本 Feature 在 `.specify/features/123-outbound-ssrf-guard/` 下产出 spec/plan/tasks。
- Blueprint `harness-and-context.md` 需新增"出站 URL 安全（SSRF 预检）"小节（living-docs 漂移闸 completion gate 处理）。

## 范围评估

受影响生产文件：`capability_pack.py`（改）+ `url_safety.py`（新）+ `config_schema.py`（加 SecurityConfig）+ `octoagent.yaml.example`（注释）= **3 改 1 新，1 个模块**。属快速修复（fix）模式适用范围（< 10 文件 / ≤ 3 模块）。
