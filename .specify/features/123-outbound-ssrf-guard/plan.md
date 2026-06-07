# F123 出站 URL SSRF 预检 — 修复规划（Phase 2 plan）

> 输入：`fix-report.md` 推荐方案 A。FIX 模式 → 最小化变更范围 + 回归风险评估 + 验证方案。
> 行为变更说明：本 Feature **有意改变出站请求行为**（新增拦截），非"行为零变更"重构。改变仅作用于"指向内网/元数据的 URL"，对正常公网 URL 行为不变。

## 变更清单（生产代码）

### C1 新增 `apps/gateway/src/octoagent/gateway/harness/url_safety.py`（安全模块）

公共 API（被 capability_pack 消费 + 单测直测）：

| 符号 | 签名 | 职责 |
|------|------|------|
| `UnsafeUrlError` | `class UnsafeUrlError(RuntimeError)` | 被拦截时抛出；子类 RuntimeError → 既有 tool-error 事件路径与 `is_error` 语义不变 |
| `ensure_url_safe` | `(url: str) -> str` | 同步全量校验，返回 normalized url，不安全 raise。含阻塞 getaddrinfo |
| `async_ensure_url_safe` | `async (url: str) -> str` | `await asyncio.to_thread(ensure_url_safe, url)`，async 热路径用，不阻塞 event loop |
| `is_always_blocked_url` | `(url: str) -> bool` | 元数据 floor 判定（字面量 + 解析），供需要的 caller 复用 |
| `reset_allow_private_cache` | `() -> None` | 仅测试：重置 yaml 开关缓存 |

内部：
- `_ALWAYS_BLOCKED_IPS` / `_ALWAYS_BLOCKED_NETWORKS` / `_BLOCKED_HOSTNAMES`：云元数据地板（见 fix-report 清单）。
- `_CGNAT_NETWORK = ip_network("100.64.0.0/10")`：`ipaddress.is_private` **不覆盖**，显式拦。
- `_is_blocked_ip(ip)`：is_private / is_loopback / is_link_local / is_reserved / is_multicast / is_unspecified / CGNAT；IPv4-mapped IPv6（`ip.ipv4_mapped`）按嵌入 IPv4 判。
- `_allow_private_urls() -> bool`：env `OCTOAGENT_ALLOW_PRIVATE_URLS`（优先，true/1/yes vs false/0/no）→ yaml `security.allow_private_urls`（best-effort `load_config(project_root_from_env)`，模块级缓存）→ 默认 False。
- `_resolve_host(hostname) -> list[str]`：DNS seam，默认 `socket.getaddrinfo`；**可 monkeypatch**（测试 hermetic）。

校验流程（`ensure_url_safe`）：
1. `url.strip()` → `urlparse`；`scheme∉{http,https}` 或 host 空 → raise。
2. host 命中 `_BLOCKED_HOSTNAMES` → raise（**always，忽略开关**）。
3. host 是**字面量 IP**：命中 always-blocked → raise；否则 `not allow_private and _is_blocked_ip` → raise。（无 DNS）
4. host 是**hostname**：`allow = _allow_private_urls()`；`_resolve_host` 解析；解析失败 → raise（fail-closed）；逐 IP：always-blocked → raise；`not allow and _is_blocked_ip` → raise。
5. 返回 normalized url。

> **floor 不可绕过**：第 2/3 步的字面量元数据判定 + 第 4 步逐 IP 的 always-blocked 判定，在 `allow_private_urls=true` 时**仍执行**。即"元数据即便开开关也拦"。开关只放开"普通私网 IP（步骤 3/4 的 `_is_blocked_ip` 分支）"。

### C2 改 `capability_pack.py`

- **删除** `_validate_remote_url`（1836-1841，死代码不留），import `url_safety`。
- `_fetch_browser_page`（1855）：
  - `normalized_url = await async_ensure_url_safe(url)`（预检，替代旧 `_validate_remote_url`）。
  - httpx client 加 `event_hooks={"request": [_ssrf_request_hook]}`；`_ssrf_request_hook(request)` → `await async_ensure_url_safe(str(request.url))`。覆盖初始 + 每跳 302。
- `_search_web`（1982）：同样 client 加 redirect hook（初始 DDG URL 也过 hook，公网无害）。保持单一硬化出站路径。

### C3 改 `config_schema.py`

- 新增 `class SecurityConfig(BaseModel)`：`allow_private_urls: bool = Field(default=False, description=...)`。
- `OctoAgentConfig` 加 `security: SecurityConfig = Field(default_factory=SecurityConfig, ...)`。向后兼容（存量 yaml 无 security 段 → 默认安全）。

### C4 改 `octoagent.yaml.example`

- 加注释化 `security:` 段示例 + 安全警告（默认 false；元数据永不放开）。

## 回归风险评估

| 风险 | 影响 | 缓解 |
|------|------|------|
| **R1 既有 browser 测试引入真实 DNS** | `test_capability_pack_tools.py` 用 `https://example.com` + MockTransport，原本离线 | monkeypatch `url_safety._resolve_host` 返回公网 IP（如 93.184.216.34），保持 hermetic + 走安全路径 |
| **R2 event_hooks kwarg 打破 mock client** | 测试 patch `httpx.AsyncClient` | 已确认：`_FakeSearchAsyncClient.__init__(**kwargs)` 吞 kwargs；MockTransport+真 client 支持 hooks。`client_factory(*a,**kw)` 透传 OK |
| **R3 UnsafeUrlError 类型** | 若有测试 assert `RuntimeError` | `UnsafeUrlError(RuntimeError)` 保持 isinstance 兼容；tool-error 路径不变 |
| **R4 async getaddrinfo 阻塞 event loop** | 性能 | `async_ensure_url_safe` 用 `asyncio.to_thread` |
| **R5 yaml 开关读取 import 环** | 启动 | `load_config` 函数内**惰性** import + try/except 兜底 False |
| **R6 web.search 其他真实调用方** | 7 个测试文件 ref web.search | 多为 catalog 断言；真实路径用 fake client（不触 hook）。全量回归验证 |

## 验证方案

- **新单测** `apps/gateway/tests/test_url_safety.py`（直测 url_safety 模块，hermetic，monkeypatch `_resolve_host`）：
  - 字面量元数据（169.254.169.254 / .170.2 / .169.253 / 100.100.100.200 / metadata.google.internal）被拦；
  - hostname 解析到元数据被拦；
  - 各私网段（10/172.16/192.168）、loopback（127.0.0.1/::1）、link-local（169.254/fe80::）、CGNAT（100.64.x）被拦；
  - IPv4-mapped IPv6（::ffff:169.254.169.254、::ffff:10.0.0.1）被拦；
  - 公网（字面量 + hostname 解析公网 IP）放行；
  - `allow_private_urls=true`（env）：普通私网放行，**元数据仍拦**；
  - 非法 scheme（file:// / javascript:）拦；DNS 解析失败 fail-closed 拦。
- **集成测** `apps/gateway/tests/test_capability_pack_tools.py` 新增：`web.fetch` 公网 URL 302→内网（MockTransport handler 返回 `Location: http://169.254.169.254/`）被 redirect hook 拦（`is_error=True`）。
- **既有测试**：browser e2e 加 `_resolve_host` monkeypatch；全量 `pytest` 0 regression vs 543a93b。
- **e2e_smoke**：`pytest -m e2e_smoke` 必过（含 #11 ThreatScanner / safety gate 域）。worktree PYTHONPATH 锁定（见 tasks T0）。
- **Codex adversarial review**：安全敏感强制；finding 闭环 0 HIGH。

## Spec 影响

- 无既有 spec 覆盖出站网络安全；本 Feature 自带 fix-report/plan/tasks。
- Blueprint `harness-and-context.md` 新增"出站 URL 安全（SSRF 预检）"小节 + `milestones.md` F123✅（completion gate / 漂移闸处理）。
