# F123 出站 URL SSRF 预检 — 修复任务（Phase 2 tasks）

> 顺序：T0 环境 → T1 安全模块 → T2 chokepoint 接入 → T3 配置 schema → T4 测试 → T5 验证 → T6 review → T7 文档/收口。
> AC↔test 显式绑定（SDD 强化）：每条 AC 紧邻标注覆盖它的 test。verify 阶段机械校验 test 存在且 PASS。

## 验收标准（AC）与 test 绑定

| AC | 描述 | 绑定 test |
|----|------|-----------|
| **AC-1** | 字面量云元数据端点（169.254.169.254 / .170.2 / .169.253 / fd00:ec2::254 / 100.100.100.200 / metadata.google.internal / metadata.goog）被拦 | `test_url_safety.py::test_literal_metadata_blocked` |
| **AC-2** | 各私网段被拦：RFC1918(10/172.16/192.168)、loopback(127.0.0.1/::1)、link-local(169.254/fe80::)、CGNAT(100.64.x) | `test_url_safety.py::test_private_ranges_blocked` |
| **AC-3** | IPv4-mapped IPv6（::ffff:169.254.169.254 / ::ffff:10.0.0.1）被拦 | `test_url_safety.py::test_ipv4_mapped_ipv6_blocked` |
| **AC-4** | hostname 解析到内网/元数据被拦（monkeypatch `_resolve_host`） | `test_url_safety.py::test_hostname_resolving_to_internal_blocked` |
| **AC-5** | 公网放行（字面量公网 IP + hostname 解析公网 IP） | `test_url_safety.py::test_public_url_allowed` |
| **AC-6** | `OCTOAGENT_ALLOW_PRIVATE_URLS=true` 时普通私网放行 | `test_url_safety.py::test_allow_private_toggle_permits_private` |
| **AC-7** | 开关开启时**元数据仍拦**（floor 不可绕过） | `test_url_safety.py::test_metadata_always_blocked_even_with_toggle` |
| **AC-8** | 非法 scheme（file:// / javascript: / 空 host）拦 | `test_url_safety.py::test_invalid_scheme_blocked` |
| **AC-9** | DNS 解析失败 fail-closed 拦 | `test_url_safety.py::test_dns_failure_fails_closed` |
| **AC-10** | 公网 URL 302 重定向到内网/元数据被 redirect hook 拦（`web.fetch` is_error=True） | `test_capability_pack_tools.py::test_web_fetch_redirect_to_internal_blocked` |
| **AC-11** | yaml `security.allow_private_urls` 解析正确 + 向后兼容（无 security 段→False） | `test_config_schema*.py::test_security_config_*`（或 url_safety 测试覆盖 yaml 分支） |
| **AC-12** | 0 regression：全量 pytest vs 543a93b baseline | 全量 `pytest`（数量对比） |
| **AC-13** | e2e_smoke 通过（含 #11 ThreatScanner / safety gate） | `pytest -m e2e_smoke` |

## 任务

- **T0 环境**：确认 worktree PYTHONPATH 锁定（`.venv` symlink 主仓 → 裸 pytest 跑 master src，须 `PYTHONPATH=<worktree>/octoagent/...` 锁 worktree，防假 0 regression）。先跑一次 baseline 子集确认能跑 worktree 代码。
- **T1 安全模块**：写 `harness/url_safety.py`（C1 全部符号 + 校验流程）。中文注释，遵守项目规范。
- **T2 chokepoint 接入**：改 `capability_pack.py`（删 `_validate_remote_url`；`_fetch_browser_page` 预检 + redirect hook；`_search_web` redirect hook）。
- **T3 配置 schema**：`config_schema.py` 加 `SecurityConfig` + `OctoAgentConfig.security`；`octoagent.yaml.example` 注释示例。
- **T4 测试**：`test_url_safety.py`（AC-1~9, AC-11 yaml 分支）；`test_capability_pack_tools.py` 加 AC-10 + 既有 browser 测试 `_resolve_host` monkeypatch（R1）。
- **T5 验证**：focused 新测试 PASS → 全量回归（AC-12）→ e2e_smoke（AC-13）。
- **T6 Codex review**：`/codex:adversarial-review` 安全敏感强制；high/medium 闭环（0 HIGH 残留）。
- **T7 收口**：completion-report.md；blueprint `harness-and-context.md` + `milestones.md` 同步（living-docs 漂移闸）；commit（不 push）；归总报告等用户拍板。

## 范围排除（明示）

- DNS rebinding (TOCTOU) 连接级防护 → 标 limitation，超 v0.1。
- 出站 tool 结果**内容**扫描 → F108。
- `web.search` host 非 LLM 可控，仅享 redirect hook defense-in-depth，不做额外 host 白名单。
