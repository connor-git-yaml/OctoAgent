# F123 出站 URL SSRF 预检 — 验证报告（Phase 4）

> baseline: origin/master @ 543a93b。所有 pytest 用 worktree-locked PYTHONPATH 执行
> （worktree `.venv` symlink 主仓，裸 pytest 会跑 master src 造成假 0 regression）。

## AC ↔ test 确定性 traceability（机械校验：test 存在且 PASS）

| AC | 绑定 test | 结果 |
|----|-----------|------|
| AC-1 字面量云元数据被拦 | `test_url_safety.py::test_literal_metadata_blocked`（7 参数）| ✅ PASS |
| AC-2 私网/loopback/link-local/CGNAT 被拦 | `test_url_safety.py::test_private_ranges_blocked`（9 参数）| ✅ PASS |
| AC-3 IPv4-mapped IPv6 被拦 | `test_url_safety.py::test_ipv4_mapped_ipv6_blocked`（3 参数）| ✅ PASS |
| AC-4 hostname 解析到内网/元数据被拦 | `test_url_safety.py::test_hostname_resolving_to_internal_blocked` | ✅ PASS |
| AC-5 公网放行 | `test_url_safety.py::test_public_url_allowed` | ✅ PASS |
| AC-6 开关放开普通私网 | `test_url_safety.py::test_allow_private_toggle_permits_private` | ✅ PASS |
| AC-7 开关开启元数据仍拦 | `test_url_safety.py::test_metadata_always_blocked_even_with_toggle` | ✅ PASS |
| AC-8 非法 scheme/空 host 拦 | `test_url_safety.py::test_invalid_scheme_blocked`（6 参数）| ✅ PASS |
| AC-9 DNS 失败 fail-closed | `test_url_safety.py::test_dns_failure_fails_closed` + `test_dns_empty_result_fails_closed` | ✅ PASS |
| AC-10 302→内网被 redirect hook 拦 | `test_capability_pack_tools.py::test_web_fetch_redirect_to_internal_blocked` | ✅ PASS |
| AC-11 yaml 开关解析 + 向后兼容 + env 优先 | `test_url_safety.py::test_security_config_yaml_parsed` / `test_security_config_backward_compat_default_false` / `test_yaml_toggle_drives_allow_private` / `test_env_overrides_yaml_toggle` | ✅ PASS |
| AC-12 0 regression | 全量 gateway non-e2e 计数对比 | ✅ PASS（见下）|
| AC-13 e2e_smoke 通过 | `pytest -m e2e_smoke` | ✅ PASS（8 passed）|

补充（review 后新增）：AC-5b 翻译形态公网放行 + 翻译形态内嵌内网拦截
（`test_public_translation_forms_allowed` / `test_translation_forms_wrapping_internal_blocked`）；
AC-6b 开关开启本机地址仍拦（`test_always_unsafe_blocked_even_with_toggle`）；
AC-11b mtime 失效（`test_yaml_toggle_mtime_invalidation`）；async 包装等价性。
`test_url_safety.py` 合计 **50 passed**。

## AC-12 0 regression 证据（review 修复后终值）

| | passed | skipped | xfailed | xpassed | failed |
|--|--------|---------|---------|---------|--------|
| baseline master 543a93b（gateway non-e2e）| 1719 | 1 | 1 | 1 | **0** |
| F123 worktree（gateway non-e2e）| 1770 | 1 | 1 | 1 | **0** |
| 差值 | **+51** | 0 | 0 | 0 | 0 |

+51 = 本 Feature 新增 test（50 url_safety + 1 AC-10）。skipped/xfailed/xpassed 完全一致
（xpassed 是既有 flaky `test_subagent_management_*` xfail，与 F123 无关，master 上同样 xpass）。
**结论：0 regression。**

命令（两侧同选择）：
`pytest apps/gateway/tests -m "not e2e_smoke and not e2e_full and not e2e_live" -q`
- worktree：`PYTHONPATH=<worktree src*> .venv/bin/python -m pytest ...` → 1756 passed
- baseline：主仓 `octoagent/.venv/bin/python -m pytest ...`（裸，走 master src）→ 1719 passed

## AC-13 e2e_smoke 证据

`PYTHONPATH=<worktree src*> .venv/bin/python -m pytest -m e2e_smoke -q` → **8 passed, 3890 deselected, 3.21s**。
含 #11 ThreatScanner / safety gate 域（`test_e2e_safety_gates.py`）。3.2s 说明 smoke 走 DI stub（F087 设计），
本 fix 改动的出站 web/browser 路径不在 smoke 域内，未引入回归。

## 行为变更声明（非"行为零变更"）

本 Feature **有意改变出站请求行为**：指向私网/loopback/link-local/CGNAT/云元数据的 URL
（含经 302 重定向到达的）现在被拦截抛 `UnsafeUrlError`（→ tool is_error=True）。对正常公网 URL
行为不变（既有 browser e2e 测试 + 全量回归证明）。

## 对抗审查（Phase 4 panel，多评审）

详见 `codex-review.md`。
- **Codex round-1**（GPT-5.4）：2 HIGH + 1 MED → F2 修 / F3 修 / F1 预授权诚实归档。
- **独立 Claude 安全审查**（Opus，empirical）：0 HIGH；MED NAT64/6to4 → 修；2 LOW → 处置。
- **Codex round-2**：MED NAT64/6to4 公网误拦（round-1 修复引入的回归）→ `_effective_ip` 修 + 正反测试。
- **Codex round-3**：HIGH IPv6 zone-id 绕过 floor → `_try_parse_ip` 剥 scope 修 + AC-7b。
- **Codex round-4**：**verdict = approve / safe-to-ship，No material findings**（收敛）。
- 闸门：**0 HIGH 残留**（F2 + round-3 修；F1 预授权 limitation）。安全敏感，CLAUDE.local.md 强制。

## 已知 limitation（spec 明示，非缺陷）

- **DNS rebinding (TOCTOU)**：预检 getaddrinfo 与 httpx 实连 getaddrinfo 之间，攻击者控的
  TTL=0 DNS 可换 IP。彻底修需连接级校验（egress proxy / pinned-IP connector），超 v0.1 范围。
  v0.1 已挡：字面量内网/元数据 IP、hostname 解析后首检、重定向逐跳重校验。
- 出站 tool 结果**内容**扫描（web/MCP/terminal 输出裸进上下文）属 F108，不在本范围。
