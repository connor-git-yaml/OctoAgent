# F123 出站 URL SSRF 预检 — completion-report

> 分支 `feature/123-outbound-ssrf-guard`（基于 origin/master @ 543a93b）。模式：spec-driver-fix（4 阶段）。
> 安全敏感 Feature：Codex 对抗 review 3 轮 + 独立 Claude 安全审查，0 HIGH 残留。

## 解决的问题（用户视角）

LLM 可被诱导让 OctoAgent 出站抓取**内网 / 云元数据端点**（SSRF）——直接抓
`169.254.169.254` 偷云实例 IAM 凭证、或用公网 URL 经 302 重定向绕进内网。此前唯一
"侥幸没被打中"的原因是部署侧碰巧没有可达内部服务；**一旦上云部署即可被直接利用偷凭证**。
F123 在所有出站 web/browser 工具前加 SSRF 预检 + 重定向逐跳重校验，封堵该攻击面。

## 实际做了 vs 计划（plan.md C1-C4）

| 计划 | 实际 | 偏离 |
|------|------|------|
| C1 新增 `harness/url_safety.py` | ✅ | review 后调整：删 `is_always_blocked_url`（无 caller，no-dead-code）；`_normalize_ip`→`_embedded_ipv4`/`_effective_ip`（覆盖 NAT64/6to4） |
| C2 `capability_pack.py` 接入 | ✅ 删 `_validate_remote_url` + `_fetch_browser_page` 预检 & redirect hook + `_search_web` hook + 模块级 `_ssrf_request_hook` | 无 |
| C3 `config_schema.py` SecurityConfig | ✅ `SecurityConfig.allow_private_urls` + `OctoAgentConfig.security` | 无 |
| C4 `octoagent.yaml.example` 注释 | ✅ | 无 |

最终改动：**3 改 + 1 新 production 文件** + 2 测试文件 + 4 spec/doc + 2 blueprint：
- `harness/url_safety.py`（新，~210 行）
- `services/capability_pack.py`（删 7 行旧校验，加预检+2 处 hook+1 helper）
- `services/config/config_schema.py`（+SecurityConfig）
- `octoagent.yaml.example`（+security 段注释）
- `tests/test_url_safety.py`（新，50 测试）+ `tests/test_capability_pack_tools.py`（+AC-10 redirect 拦截 + browser 测试 DNS stub 保 hermetic）
- blueprint：`milestones.md`（F123✅）+ `codebase-architecture/harness-and-context.md`（§2.7 出站 URL 安全）

## Phase 执行（spec-driver-fix 4 阶段）

- **[1/4] 诊断**：5-Why 根因 = `_validate_remote_url` 仅检 scheme/netloc 无 IP 拦截 + `follow_redirects=True` 无逐跳校验。攻击面 web.fetch + browser.open/navigate/act 全汇聚到单一 chokepoint `_fetch_browser_page`。
- **[2/4] 规划**：方案 A（新安全模块 + chokepoint 接入 + redirect hook + 开关）。GATE_DESIGN AUTO_CONTINUE。
- **[3/4] 修复**：见上 C1-C4。
- **[4/4] 验证**：见下"验证结果" + "review 闭环"。

## 验证结果

| 项 | 结果 |
|----|------|
| `test_url_safety.py` | **54 passed**（AC-1~11 + AC-5b/6b/7-NAT64/7b-zoneid/11b + async 等价）|
| AC-10 redirect 拦截 | `test_web_fetch_redirect_to_internal_blocked` PASS（302→元数据，内网响应未读）|
| **0 regression** | master baseline **1719** → worktree **1774**（精确 +55 新测试），failed 两侧均 **0** |
| e2e_smoke（含 #11 safety gate）| **8 passed** |
| browser e2e hermetic | 保持离线（`_resolve_host` monkeypatch）|

AC↔test 绑定见 `verification/verification-report.md`（13 AC 全 PASS）。

## Codex 对抗 review 闭环（3 轮）+ 独立 Claude 安全审查

| 轮次 | finding | 处置 |
|------|---------|------|
| Codex round-1 | F1 HIGH DNS rebinding / F2 HIGH allow_private 过宽 / F3 MED 缓存 fail-open | F2 修（三层判定）/ F3 修（mtime 失效）/ F1 预授权诚实归档（见下 limitation）|
| 独立 Claude | 0 HIGH；MED NAT64/6to4 元数据绕过；2 LOW | MED 修（NAT64/6to4 解包）/ LOW 删死代码 + 注释 |
| Codex round-2 | MED：NAT64/6to4 **公网误拦**（我 round-1 修 NAT64 时引入——判了包装 IPv6 的 is_reserved/is_private）| 修（`_effective_ip` 只判内嵌真实目标 IP）+ 正反向测试 |
| Codex round-3 | **HIGH**：IPv6 zone id（`fd00:ec2::254%en0`）绕过元数据 floor（带 scope ≠ floor 字面量，toggle on fail-open）| 修（`_try_parse_ip` 剥离 zone id）+ AC-7b 测试 |
| Codex round-4 | **approve / safe-to-ship，No material findings**（收敛）| — |

**0 HIGH 残留**：F2 + round-3 已修；F1 为任务 prompt 预授权的 v0.1 limitation（非未解决 HIGH）。
多轮 review 实证"大 fix 后必须 re-review"——round-2 抓到 round-1 引入的回归、round-3 抓到 zone-id
floor 绕过，findings 逐轮趋窄（体系性 → 边角），正是 F099"至少 2-3 轮才收敛"教训。

## 已知 limitation（spec 明示，非缺陷）

- **DNS rebinding（TOCTOU）**：pre-flight 层无法根治（攻击者控 TTL=0 DNS 在预检与 httpx
  实连之间换 IP）。**未宣称修复**。彻底修需连接级 IP pinning（pinned-IP transport + 保留
  Host/SNI）或 egress proxy，**列 M6/M7 egress 域**。与 Hermes 参考实现同级。
  → **待用户拍板**：是否本期就上连接级 pinning，还是按预授权作 limitation。
- 出站 tool 结果**内容**扫描（web/MCP/terminal 输出裸进上下文）属 **F108**，不在本范围。
- `0177.0.0.1` 等平台相关八进制字面量：安全性依赖 precheck/connect 同 resolver 一致，非漏洞。
- NAT64 仅识别 well-known 前缀 `64:ff9b::/96`；运营商特定 NSP 前缀不识别（无法枚举，标准前缀已覆盖）。

## living-docs 漂移闸

本 Feature 触碰的 harness 安全层已同步 `harness-and-context.md §2.7` + `milestones.md` F123✅。
未发现其他 code↔doc 漂移。`octoagent.yaml.example` 的 `runtime:` 段含 F081 已退役的
`llm_mode/litellm_proxy_url`（**既有遗留，非本 Feature 引入**）——已 spawn 独立 follow-up，不混入本 fix。

## 下一步

commit（**不 push**）→ 归总报告 → 等用户拍板（push / DNS-rebinding 连接级 pinning 是否本期做）。
