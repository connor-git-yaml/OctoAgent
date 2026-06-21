# F106 Spec Adversarial Review — Round 1（双 panel：security red-team + Constitution/arch）

> 在 GATE_DESIGN（Model B 拍板）后、plan 前对 spec v0.2 做对抗审查（SDD 强化"多评审 panel @重大架构变更节点"）。
> 两个独立 agent（安全红队 + Constitution/架构一致性），均 code-grounded（实读 tool_registry.py / content_threat_scan.py / discovery.py / octo_harness.py）。
> 结论：**方向 sound（code-hash re-approval 闭合 Agent Zero 盲目 reload 洞是真亮点），但 spec 有 2 处 FALSE reuse 声明 + 多处 honesty 过度承诺 + git 加固缺口，须 plan 前修 + 分阶段。**

## HIGH（must-fix before plan）

| ID | 发现 | 处理 |
|----|------|------|
| H1 | **`scan_and_register` 调 `exec_module()` 执行代码**（tool_registry.py:305-365）；reuse 它与"未审批不执行"矛盾 | ✅ 改 DP-3/FR-3.2：**不 reuse scan_and_register**；写专用 plugin tool-load path；硬 FR：非 ENABLED+hash 匹配的 plugin **绝不** import/exec_module/scan；契约测试 `sys.modules` 不含其模块 |
| H2(arch) | **`scan_and_register` 忽略 `registry` 参数（no-op 语义标记），写全局 `_REGISTRY` 单例，模块名无 namespace**（:316/:192/:286） | ✅ 专用 path：namespaced 模块 `octoagent_plugins.<name>.*` + 直接 `registry.register()` |
| H3(arch) | **ToolRegistry `register()` 覆盖语义（热更新），无冲突拒绝**（:124 无条件 `_entries[name]=entry`）；FR "reject not overwrite" 是新行为且 racy（top-level register() 已覆盖） | ✅ DP-3/FR-3.2：loader staging set 预检名冲突 → 拒，再 commit；不靠 register() 自身拒 |
| H4 | **`ContentThreatScanService` 无 `scan(scope=MEMORY)` 方法**——实为 `scan_memory(content)->ThreatScanResult`（查 `.blocked`，sync）+ `scan_tool_context`（content_threat_scan.py:30-45）；oversize→degraded BLOCK（fail-closed，threat_scanner.py:579）与 spec fail-open 冲突 | ✅ 改 DP-9/FR-5：用 `scan_memory(content); reject if .blocked`（sync）；fail 模式拆分：scanner **raises**→fail-open；返回 **degraded BLOCK**（oversize）→拒载 plugin |
| H5(red) | **"plugin 不能改 Policy"非构造性**——进程内 = 全解释器访问，import 期可 monkeypatch Policy/Registry/ApprovalGate/sys.modules | ✅ §0.2 控制 5 + FR-3.3 降级为"cooperative-only 契约，**非**对恶意已审批代码的安全边界"；审批提示须明示"运行任意代码、全访问"；真边界=v0.2 沙箱 |
| H6(red) | **code_hash 仅 .py** 漏 `.so/.pyc/eval'd-data/runtime-fetch/dynamic-import` | ✅ FR-2.3：hash **整个 plugin 目录树**（全文件排序 path+content），非仅 .py；residual：runtime-fetch/eval 远程内容静态 hash 无法覆盖（honest §0.3）|
| H7(red) | **classification 可能 import + 触发器仅 .py**：`__init__.py`/`.pth`(site exec)/`conftest.py`/`__pycache__` 旁路 gate | ✅ FR-1.4：discovery **纯 stat + 文本读，绝不 import**；code-capable 触发 = 任一 `.py/.pyc/.so/.dylib/.pyd/.pyx/.pth/conftest.py/setup.py/pyproject.toml`；declarative 含这些 = 拒/标 code-capable |
| H8(red) | **git 缺注入/容纳护栏**（MCP installer 有：`_validate_package_name`/`_build_safe_env`/`_validate_install_path`）；`ext::` transport = clone RCE | ✅ 新 DP-12/FR-7 硬化：scheme allowlist（https/ssh）禁 `ext::`/`fd::`/`file://`；`--` 终止符；`-c protocol.ext.allow=never -c core.hooksPath=/dev/null -c core.fsmonitor=false`；scrub env；temp-then-move；拒 symlink-`.git`；clone target `_ensure_path_within`+kebab |
| H9(red) | **watchdog TOCTOU/ordering race**：detect-after-load / 半写 hash / A import 未审批 B | ✅ FR-6.3：变更检测先 **deregister 工具 + evict sys.modules（dispatcher lock 下）**再转 pending_approval；hash 仅 quiesced tree（debounce-until-stable / git rev-parse）；cross-plugin import 不支持（residual）|

## MED

| ID | 发现 | 处理 |
|----|------|------|
| M1(red) | threat-scan-only-declarative = false confidence；审批 UX 不得显"safe/scanned" | ✅ FR-8.3：审批面禁"已扫描/安全"字样，须显式风险披露（绑 provenance）|
| M2(red) | spec 过度承诺安全；residual 欠明 | ✅ 新 §0.3 Residual Risk（无隔离/全访问/hash 漏/用户唯一边界）|
| M3(red) | secrets 暴露（#5）：进程内 plugin 读 os.environ/.env/credential store | ✅ §0.3 + 审批披露；v0.2 沙箱真修 |
| M4(red) | 降级洞：atexit/thread、失败 import 留毒 sys.modules、watchdog reload loop、behavior cache DoS | ✅ FR-4.2：失败 `sys.modules.pop`；FR-6：watchdog 自限流 + 忽略 loader 自身写 + 忽略 plugin 自写循环；behavior overlay debounce |
| M5(arch) | WriteResult 契约仅 produces_write=True 时触发；谎报可绕 | ✅ §7 #3 honest：correctness guardrail（对 honest-but-buggy），非对恶意 plugin 的边界 |
| M6(arch) | **#4 honest @enable 粒度，但 SC-004"plugin 工具走 Policy"在进程内不可交付**（handler 可直接 open/socket/subprocess）| ✅ **最大 honesty gap**：SC-004 降级"仅对 plugin 主动以注册工具表达的 side-effect"；§0.2 控制 5 同改 |
| M7(arch) | H1 FR-11.1"MUST NOT register channel"是 requested 非 enforced（code plugin 可直接调 PlatformRegistry）| ✅ FR-11.1/SC-011 改"无 *supported* surface 绕过主 Agent；code plugin 非运行时强制，靠 trust+audit；enforcement=v0.2 沙箱" |
| M8(arch) | `POST /{name}/update` orphan（FR-7/DP-7/US5 引用，FR-8 列表缺）| ✅ 加 FR-8.9 |
| M9(arch) | 审批勿学 `behavior.write_file` confirmed（已知 LLM 自批嫌疑）| ✅ FR-2.2/8.3：审批 = 独立 human-initiated HTTP 请求，**绝非** LLM 同轮可填 flag |

## LOW

- L1(red) provenance 未验证（manifest `repo:` 攻击者可填）→ 展示从实际 `.git` 取，非 manifest；标注 audit-only。
- L2(red) clone target name 校验缺（仅 DELETE 有）→ 套 `_ensure_path_within`+kebab（已并入 H8）。
- L10(arch) recon stale path（capability_pack 在 `apps/gateway/.../services/` 非 packages/skills）→ plan 重核行号。
- L11(arch) SkillSource.PLUGIN 优先级：SkillDiscovery 是 last-writer-wins+log 无 reject → PLUGIN 扫**最先**（最低优先级）+ loader 显式预检拒。
- L12(arch) AC↔test 缺：no-plaintext-audit / per-reason 发射 / shutdown observer+on_unload race → §9 补。

## 分阶段（两 reviewer 独立收敛，全在 F106 下，非拆 Feature）

- **Phase A — declarative spine（最小安全切片，无代码执行/watchdog/git）**：plugins_dir DI + 段7.5 + PluginManifest + 能力分类（纯 stat）+ declarative skill 注册（PLUGIN 最低优先级 + 冲突拒）+ scan_memory 威胁扫描 + KNOWLEDGE.md fallback overlay + PLUGIN_LOADED/REJECTED 事件 + REST list/get/toggle/delete + 降级隔离。**数据 only，结构性 H1-safe，零任意代码风险**。闭 SC-001/005/006(skill)/007/010/011/012。
- **Phase B — code-capable + 审批 + code-hash（安全核心）**：专用 plugin tool-load path（namespaced + 直接 register + staging 冲突拒，**非** scan_and_register）+ 整树 code_hash + `POST /approve`（human-initiated）+ pending_approval 惰性不 import + import 隔离 + hooks + honest 限制语言。**dual-review 预算重点在此；Gate B 过 review 再 Phase C**。闭 SC-002/004/006(tool)。
- **Phase C — watchdog + git（建于 code-hash）**：observer（declarative 自动 + code 变更→pending_approval）+ git 硬化 install/update + PLUGIN_CODE_CHANGED + shutdown teardown 测试。闭 SC-003/008/009。

## 总评

方向 sound，code-hash re-approval 是真改进。must-fix（H1-H9 + M6 honesty）后，"无沙箱 trust-on-install 单用户"是**可辩护的 v0.1**——前提是停止假装 4 控制"容纳"威胁，改为诚实地"门控首次执行 + 整树 hash 闭合换码 + 显式 residual risk + 真隔离推 v0.2 沙箱"。采纳 Phase A/B/C 分阶段。
