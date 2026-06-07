# F123 Codex 对抗 review finding 闭环

> Codex GPT-5.4，working-tree scope。verdict: needs-attention（3 finding：2 HIGH + 1 MED）。
> 处理原则：决策集中（主 session 评估，不自动接受）；安全敏感 → 闭环到 0 HIGH 残留。

## F1 [HIGH] DNS rebinding：hostname SSRF 仍可 TOCTOU 绕过（url_safety.py ensure_url_safe）

**Codex 论点**：预检 `_resolve_host` 判定后只返回原始 URL，httpx 再对同 hostname 重新解析连接；
攻击者控 DNS 时可预检返公网 IP、连接返内网 IP，redirect hook 也只是把同样 TOCTOU 窗口移到每跳前。

**裁决：接受（作 v0.1 已知 limitation，连接级修复 defer）—— 但收敛"诚实声明"**
- 用户在任务 prompt **已预授权**："v0.1 至少挡直连 IP + 重定向；DNS rebinding 可标 limitation"。
- 参考实现 Hermes 同样把 DNS rebinding 列为 limitation，未在 pre-flight 层修。本实现与参考对等。
- 真彻底修需连接级 IP pinning（pinned-IP transport + 保留 Host/SNI）或 egress proxy，涉 TLS/SNI
  细节，security-critical 路径加复杂度风险高，超 v0.1 范围。M6 调研已把 egress/连接级列为后续。
- **闭环动作**：①spec/acceptance/blueprint 不得宣称"hostname SSRF 已修复"，明确表述为"挡住：字面量
  内网/元数据 IP + 静态 DNS 解析到内网 + 每跳重定向目标；DNS-rebinding TOCTOU 为已知 limitation，
  连接级 pinning 列为 future（F-future / M7 egress 域）"。②归总报告向用户显式标注此 HIGH 的 defer
  及理由，由用户最终拍板是否本期实现 pinning。

## F2 [HIGH] allow_private_urls=true 会放开 loopback/link-local/unspecified 等本机目标

**Codex 论点**：`_reject_ip_if_unsafe` 在 allow_private=true 时整体跳过 `_is_blocked_ip`，而后者除
RFC1918 还含 loopback / IPv6 link-local / reserved / multicast / unspecified / CGNAT。开开关后
LLM 可直接打 127.0.0.1 / ::1 / 0.0.0.0 / fe80:: 等本机控制面，SSRF 防线对大量高价值内网服务失效。

**裁决：接受并修复（比 Hermes 更严，正确）**
- 开关初衷仅服务"DNS 把外域解析到私网/benchmark 段（RFC1918 / 198.18 / CGNAT）"的特殊部署，
  **不应**放开 loopback / unspecified / multicast / link-local / reserved —— 这些任何部署都非合法目标。
- **修复**：拆分判定——
  - `_is_always_unsafe_ip`（**忽略开关**始终拦）：loopback / link_local / multicast / unspecified / reserved。
  - `_is_toggle_openable_private_ip`（仅开关开时放行）：RFC1918/benchmark（`is_private` 去掉上面已拦项）+ CGNAT。
  - 元数据 floor（`_is_always_blocked_ip`）保持最高优先。
- 测试：更新 AC-6（开关开时 10.0.0.1 放行、127.0.0.1 仍拦）+ 新增 AC-6b（开关开时
  127.0.0.1/::1/0.0.0.0/fe80:: 仍拦）。

## F3 [MEDIUM] YAML 开关缓存无失效 → 关开关需重启（fail-open 回滚）

**Codex 论点**：`_yaml_allow_private_urls` 模块级缓存首次读到的值，生产无失效；运维把 yaml 改回
false 后当前进程仍按 true 放行私网直到重启/env 覆盖。

**裁决：接受并修复（mtime 失效）**
- **修复**：缓存键加入 octoagent.yaml 的 `(path, mtime_ns)`；每次 stat（廉价），mtime 变化即重读
  （load_config 全量解析较重，不能每次裸读；stat 足够廉价）。config.apply/改文件→mtime 变→立即生效。
- 测试：新增 AC-11b——yaml true 放行 10.0.0.1 后改 false（os.utime 强制新 mtime），**不调** reset
  缓存，10.0.0.1 立即被拦。

## 独立 Claude 安全审查（第二评审，与 Codex 交叉核对）

empirical review（实跑 worktree 代码验证向量）。**0 HIGH**。
- **MED NAT64/6to4 元数据绕过（仅 toggle on）**：`[64:ff9b::a9fe:a9fe]` / `[2002:a9fe:a9fe::1]`
  解码为 169.254.169.254 但 is_global，元数据地板漏判。→ **接受并修复**（`_embedded_ipv4` 解包
  NAT64/6to4 内嵌 IPv4，floor/always-unsafe/private 全复用；AC-7 加用例）。
- **LOW is_always_blocked_url 无 caller**：→ **接受，删除**（no-dead-code 规范）。
- **LOW 0177.0.0.1 平台解析**：非漏洞（precheck/connect 同 resolver 一致）→ 注释说明，不改逻辑。
- 逐条确认：redirect hook 每跳触发（实测 3 跳）、fail-closed 一致、`_search_web` 无误伤、
  删 `_validate_remote_url` 零悬挂、spec↔code 对齐、Constitution #5/#9/#10 合规。

## Codex round-2（复核 round-1 修复 delta）

**MED：NAT64/6to4 公网目标被误判为内部（我 round-1 修 Claude-MED 时引入的回归）**
- `_check_ips` 同时判"包装 IPv6 + 内嵌 IPv4"，而 `_is_always_unsafe_ip`/`_is_toggle_openable_private_ip`
  会据**包装地址**的 is_reserved/is_private 判定。`64:ff9b::0808:0808`（→公网 8.8.8.8）的包装前缀
  is_reserved=True、`2002:0808:0808::1` 包装 is_private=True → 公网 NAT64/6to4 被误拦，违背"正常公网行为不变"。
- **裁决：接受并修复**——引入 `_effective_ip(ip)`（翻译形态→内嵌 IPv4，否则原样），三个判定函数
  全改为只判 effective IP（包装前缀的 IANA 属性非目标属性）。元数据 floor 唯一 IPv6 字面量
  fd00:ec2::254 非翻译形态、effective 原样命中，不受影响。
- 测试：AC-5b 新增（公网 NAT64/6to4/IPv4-mapped 放行 + 内嵌 loopback/private/metadata 仍拦）。

## Codex round-3（复核 `_effective_ip` delta）

**HIGH：IPv6 zone identifier 绕过 fd00:ec2::254 always-block floor**
- `http://[fd00:ec2::254%25en0]/` → `ipaddress.ip_address` 生成带 scope_id 的 IPv6Address，
  `!= _ALWAYS_BLOCKED_IPS` 里无 scope 的字面量 → `allow_private_urls=true` 时 fail-open
  （floor 不变量"元数据永远拦"被破）。
- **裁决：接受并修复**——`_try_parse_ip` 在 `ipaddress.ip_address` 前 `value.split("%",1)[0]`
  剥离 zone id（本地接口选择器，对 SSRF 分类无意义）。所有 IP 解析单点经此，统一生效。
- 测试：AC-7b（zone id + 元数据/NAT64/link-local/loopback，toggle on 仍拦）。

## 状态

- [x] **F2（HIGH）修** + 测试（三层判定，AC-6 更正 + AC-6b 7 例）。
- [x] **F3（MED）修** + 测试（yaml `(path,mtime_ns)` 失效，AC-11b）。
- [x] **F1（HIGH）诚实归档**（预授权 limitation，docstring/spec/blueprint 收敛 + 归总报告标注）。
- [x] **Claude MED（NAT64/6to4 floor）修** + **2 LOW 处置**（删死代码 + 注释）。
- [x] **Codex round-2 MED（NAT64/6to4 公网误拦）修**（`_effective_ip`）+ AC-5b 正反测试。
- [x] **Codex round-3 HIGH（zone id 绕过 floor）修**（`_try_parse_ip` 剥 scope）+ AC-7b 测试。
- [x] 修复后全量回归 **1774 passed**（baseline 1719 + 55 新测试）0 regression；e2e_smoke 8 passed。
- [x] **Codex round-4：verdict = approve / safe-to-ship，No material findings**。zone-id 修复覆盖
  字面量 + DNS 两路；NAT64/6to4+zone、scoped link-local 均拦；parse 收尾扫描无剩余缝隙
  （NUL/空白/超长/DNS 异常 fail-closed；@/反斜杠 host 与 httpx 0.28.1 一致；hex/dec/short IPv4
  同 resolver 归一；IDN/unicode dot fail-closed 或解析后按 IP 判）。

## 闭环结论（已收敛）

**4 轮 Codex + 1 独立 Claude 审查，round-4 = approve / 0 HIGH 残留。** findings 逐轮趋窄
（round-1 体系性 → round-3 zone-id 边角 → round-4 无），F1 为任务 prompt 预授权 limitation。
多轮 re-review 实证价值：round-2 抓 round-1 引入的回归、round-3 抓 zone-id floor 绕过——正是
F099"大 fix 后必须 re-review、至少 2-3 轮才收敛"教训。可 commit。
