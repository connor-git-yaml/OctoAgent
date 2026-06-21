# Feature Specification: F106 User Plugin Loader

**Feature ID**: F106
**Feature Branch**: `feature/106-plugin-loader`
**Created**: 2026-06-21
**Status**: Draft v0.3（GATE_DESIGN 拍板 Model B + watchdog + git，见 §0.1/§12；**spec adversarial review round-1 双 panel 闭环**：9 HIGH + 9 MED 全修——2 处 FALSE reuse 声明纠正[scan_and_register/scan_memory] + honesty 重构[§0.3 residual] + git 硬化 + race 闭合 + Phase A/B/C 分阶段，见 [spec-review-r1.md](./spec-review-r1.md) + §13）
**M6 阶段**: M6 surface 扩张（地基 sprint 全清后第 3 件，原 Companion 改名）
**Upstream**:
- SkillDiscovery（`packages/skills/.../discovery.py`）+ skills REST（`routes/skills.py`）—— **在其上升级为 plugin_registry，不从零**
- 中央 ToolRegistry（`harness/tool_registry.py`，F084，注册期 fail-fast + WriteResult 契约）—— **plugin 注册新工具的落点**
- ApprovalGate（`harness/approval_gate.py` + F101 SSE production）—— **code plugin 启用审批的落点（#4/#7）**
- Behavior workspace（`core/behavior_workspace/`）+ `_PROFILE_ALLOWLIST` + `resolve_behavior_pack`
- F105 PlatformRegistry fail-fast（`channels/registry.py`）+ handoff §5 channel-as-plugin 构想（**本件记为扩展点，不实现**）
- F084 ThreatScanner + `ContentThreatScanService`（F124）—— 扫**声明式**制品
- Agent Zero `helpers/plugins.py`（az-2：`plugin.yaml` / `.toggle` / watchdog `purge_namespace` / git / hooks.py / 删除安全边界）
- OctoHarness 11 段 bootstrap + `plugins_dir` DI（`octo_harness.py`）
**Downstream**:
- M7 Companion（本 Feature 是其装载基础设施前置）
- channel-as-plugin（**仍推迟** = 扩展点，handoff 交接；H1 复杂度 + 任务显式"留后续"）
- F106 v0.2（沙箱/签名/per-project scoping/能力作用域权限）
**Baseline**: f3d8a267（master HEAD）
**Feature 性质**: 用户/社区插件装载子系统——**发现 + 校验 + 信任审批 + 注册 + 热重载 + git 安装/更新**。两类 plugin：①**声明式**（skill 指令 + KNOWLEDGE 知识，数据，自由装载）；②**代码可执行**（hooks/工具模块/extensions，进程内 Python，**启用须用户显式审批 + code-hash 绑定**）。单用户个人 OS 信任模型 = trust-on-install + 审批门控 + provenance + 审计，**v0.1 无沙箱**（后续加固方向）。不触碰 Agent 协作模型（H1/H2/H3）。
**研究产物**: [research/recon.md](./research/recon.md)（块 A 实测侦察 + az-2 逐项调研）

---

## 0. 设计基础说明（实测核实，master HEAD f3d8a267）

完整侦察见 [recon.md](./research/recon.md)。塑造 v0.1 的关键事实：

1. **SkillDiscovery 声明式**：`SKILL.md` = markdown 指令 + `tools_required`（引用已存在工具）；三级源 `SkillSource{BUILTIN<USER<PROJECT}`（`skill_models.py:16`），坏文件 graceful 跳过（`discovery.py:283`）；USER=`~/.octoagent/skills/`，删除仅 USER + 路径守卫（`routes/skills.py`）。
2. **中央 ToolRegistry**（F084，`harness/tool_registry.py:113`）：注册期 fail-fast（`_enforce_write_result_contract`）+ AST 扫描 builtin tools。**plugin 注册的新工具走此入口** → 自动获 Policy/ApprovalGate 治理。
3. **ApprovalGate**（`harness/approval_gate.py` + F101 SSE）：session allowlist + SSE 等待用户决策。**code plugin 启用审批复用此机制**（#4 两阶段）。
4. **Behavior pack 当前纯项目内部**；`_PROFILE_ALLOWLIST`（FULL9/WORKER8/MINIMAL4）；session 级缓存 + mtime 失效（`agent_decision.py:180`）。
5. **F105 PlatformRegistry fail-fast**（`registry.py:29`）= plugin 降级范式来源。handoff §5：channel-as-plugin = adapter 类（可执行）→ **仍推迟**（任务"留后续" + H1 复杂度）。
6. **watchdog 库不是依赖**（pyproject 无）；现有"watchdog"是 APScheduler 任务态监控。git 无 GitPython，范式 `asyncio.create_subprocess_exec`。Agent Zero `.py` 变更走 `modules.purge_namespace` 重载（**我们改为 code-hash 变更须重新审批**，见 §0.2 控制 7）。
7. **`~/.octoagent/`** 有 `data_dir`/`mcp_servers_dir` DI 覆盖；F106 需 `plugins_dir` DI。bootstrap 11 段，plugin 发现插独立段 7.5。
8. **Agent Zero plugin 执行任意 Python**（`hooks.py`/`execute.py`/`extensions/`），但**盲目 reload .py 无信任校验** → 我们补 code-hash 审批闭合该洞。

### 0.1 ★ GATE_DESIGN 用户拍板（v0.2 基线）

| 决策 | 用户选择 | 影响 |
|------|---------|------|
| DP-1 信任模型 | **Model B 代码可执行**（非保守 Model A）| plugin 可携带 Python（hooks/工具模块/extensions）；进程内执行；需真信任模型（§0.2）|
| DP-6 hot-reload | **watchdog 自动热重载** | 加 `watchdog` 依赖 + 后台 observer；**code 变更触发 re-approval**（非盲目 reload）|
| DP-7 git update | **v0.1 内置 git 安装/更新** | `subprocess` git clone/pull + provenance + code 变更 re-approval |

**与任务原文调和**：任务 scope 行写"v0.1 聚焦 skill/behavior plugin"，但任务安全行写"加载用户代码面…plugin 代码进程内执行的风险边界，spec 阶段明确 v0.1 信任模型"——用户 gate 拍板 Model B 是后者的兑现。**channel-as-plugin 仍按任务"留后续"推迟**（唯一明确推迟项 + H1 复杂度）。**沙箱按任务"后续加固方向" + 用户"v0.1 做不完沙箱"推迟**。

### 0.2 ★★ v0.1 信任模型（任务明确要求 spec 定义；单用户个人 OS）

**威胁边界**：单用户自装 plugin。威胁：①用户装了恶意/有 bug 的社区 plugin（代码面）；②plugin 声明式内容做 prompt injection（数据面）；③plugin 越权超出用户预期。

**v0.1 姿态 = trust-on-install + 审批门控（无沙箱）**。控制项：

1. **能力声明 + 纯 stat 分类（review H7）**：manifest `provides: {skills, behavior, tools, hooks, extensions}`。**分类纯文件系统 stat + 文本读，绝不 import/`__import__`/加 plugin 路径到 sys.path**（防 `__init__.py`/`.pth`/`conftest.py` import 旁路 gate）。**code-capable 触发** = 目录含任一 `*.py/*.pyc/*.so/*.dylib/*.pyd/*.pyx/*.pth/conftest.py/setup.py/pyproject.toml`；否则 **declarative**。declarative plugin 含上述任一 → 拒载或重分类（不信任 manifest 自报）。
2. **两层装载**：
   - **declarative plugin**（仅 skills + KNOWLEDGE，无 .py）：发现即装，过威胁扫描（数据，低风险）。
   - **code-capable plugin**：发现 + 列出，但**代码在用户显式审批启用前绝不 import/执行**（#4 两阶段 + #7）。未审批 = `pending_approval`（惰性，不 import）。
3. **审批 = 用户显式启用动作（独立 human-initiated 请求），绑定整树 code-hash（review H6/M9）**：审批记录 plugin **整个目录树**（全文件排序 path+content sha256，**非仅 .py**——防 `.so/.pyc`/data 文件换码）的 hash。审批 MUST 是用户独立 HTTP 请求（`POST /approve` from UI），**绝非** LLM/Agent 同轮可填的 flag（避 `behavior.write_file confirmed` 式自批）。代码/任一文件变更 → hash 变 → **强制重新审批**。**闭合"审批一次后换码"洞**（Agent Zero 盲目 reload 缺失）。**残余**：runtime-fetch/`eval` 远程内容静态 hash 无法覆盖（见 §0.3）。
4. **provenance + 审计**：`PLUGIN_LOADED` 记 name/version/source(repo+commit)/capabilities/code_hash；用户可见来源。
5. **plugin 工具经 Policy/ApprovalGate——但仅限 plugin 主动以注册工具表达的 side-effect（review H5/M6，最大 honesty gap）**：plugin 工具经专用 load path 注册进中央 ToolRegistry（注册期 fail-fast WriteResult 契约，#3）；其**作为注册工具的调用**走 Policy + ApprovalGate（#10）。⚠️ **诚实边界**：已审批 plugin 是进程内任意 Python，可直接 `open()/socket()/subprocess`、读 `os.environ`/`.env`、monkeypatch Policy/Registry/ApprovalGate——**Policy 治理不是对恶意已审批代码的强制边界**，只对"合作型 plugin 经注册工具表达的 side-effect"成立。真隔离 = v0.2 沙箱。这正是"启用=运行任意代码"的含义（§0.3）。
6. **威胁扫描限声明式制品（review H4）**：manifest + SKILL.md + KNOWLEDGE.md 过 `ContentThreatScanService.scan_memory(content)`（同步，查 `.blocked`）；**代码（.py/.so/...）不做恶意扫描**（正则扫描器非代码安全工具）——代码安全靠 trust-on-install + 审批 + provenance（不制造"已扫描=安全"假信心，控制 8）。
7. **watchdog code reload 须 re-approval**：declarative 变更自由热重载；**code 变更（.py / code-hash 变）→ plugin 转回 `pending_approval`，代码不自动 reload 执行**，等用户重新审批（闭合控制 3 的洞）。
8. **H1**：channel-as-plugin 推迟；v0.1 code plugin 可注册 tools/hooks，**不得注册 user-facing 渠道**。
9. **降级（#6）**：code plugin import/注册抛错 → 隔离拒载 + 审计 + gateway 继续。
10. **后续加固方向（非 v0.1，handoff）**：子进程/容器沙箱；代码签名；capability 作用域权限授予；per-tool 审批。

### 0.3 ★★★ 残余风险（v0.1，诚实声明 — review M2/M3/M6/H5）

**v0.1 = trust-on-install + 审批门控（无沙箱）。四个控制项门控"代码是否首次运行" + "换码是否触发重审"，但 _不容纳_ 已审批代码的行为。诚实地：**

- **无隔离**：已审批 plugin = 进程内任意 Python，对 gateway 进程**全访问**——文件系统、网络（F123 SSRF 守卫只管 Agent 的 `web.fetch` 工具，不管 plugin 里裸 `httpx`）、进程内可达的 secrets（`os.environ` 的 provider key、`~/.octoagent/.env`、credential/secret store 单例）、以及改写 Policy/Registry/ApprovalGate 的能力。
- **审批 = 完全信任授权**：启用一个 code plugin 等价于"我愿意像 `python` 一样运行这段代码"。Policy/ApprovalGate 治理只对**合作型** plugin 经注册工具表达的 side-effect 成立，不是对恶意代码的强制边界（H5/M6）。
- **code-hash 只覆盖静态文件树**：换 .py/.so/data 文件会触发重审（好），但**运行时 `httpx.get(url)` 后 `exec`、`eval(data_file)` 的内容 hash 看不见**（H6）。
- **provenance 未验证**：repo/commit 是 audit 信息非信任凭证；v0.1 不验签（H L1）。
- **#5 secrets 在 spirit 上被突破**：进程内 plugin 不是 LLM，能读所有进程内 secrets。MCP installer 的 `_build_safe_env` 子进程隔离**不适用**于进程内 plugin。
- **用户是唯一信任边界**：审批面 MUST 明示"运行任意代码、全访问你的 agent（含改安全策略与读凭证）、未做安全扫描——只启用你信任的 plugin"（FR-8.3），**禁**任何"已扫描/安全/已验证"措辞（M1）。
- **真修 = v0.2**：子进程/容器沙箱 + 签名 + capability 作用域权限。declarative plugin（无代码）无此残余——结构性安全。

> 这是有意为之的单用户 v0.1 姿态：诚实门控首次执行 + 整树 hash 闭合换码 + 显式 residual，而非假装四控制"容纳"了威胁。

---

## 1. 目标（Why）

- **1.1 surface 扩张（代码可执行）**：让用户/社区把自定义 **skill + behavior pack + 新工具/hooks** 装进 `~/.octoagent/plugins/`，主 Agent 自动发现并可用——开放真正的能力扩展（不止数据，含新工具）。
- **1.2 在现有面上升级，不重写**：复用 SkillDiscovery（skill）+ 中央 ToolRegistry（plugin 工具）+ ApprovalGate（启用审批）+ behavior overlay；扩 `SkillSource.PLUGIN` + plugin 工具注册 + behavior 低优先级源。
- **1.3 优雅降级（#6）**：单 plugin 坏（manifest 错/制品坏/威胁命中/import 抛错/名冲突）→ fail-fast 拒载该 plugin + 审计 + 隔离，gateway 正常、其他 plugin 不受影响。
- **1.4 代码执行信任模型（v0.1）**：code plugin 启用须用户显式审批（code-hash 绑定）；无沙箱但门控 + provenance + 审计；声明式制品过威胁扫描。
- **1.5 热重载 + git 分发**：watchdog 监听自动生效（code 变更 re-approval）；git clone/pull 安装/更新。
- **1.6 不绕过 Agent 协作模型**：plugin 不让渠道/Worker 绕过主 Agent（H1）；不破坏上下文栈对等（H2）；不引入新委托模式（H3）；plugin 工具不旁路 Policy（#10）。

---

## 2. 范围声明

### 2.1 In Scope（v0.1）

**发现 + manifest**
- `~/.octoagent/plugins/<name>/plugin.yaml`；`PluginManifest`（`name/version/description/author?/repo?/provides{skills[]/behavior[]/tools[]/hooks?/extensions[]}`）。
- 发现：扫 `plugins_dir/*` 含 `plugin.yaml`；能力分类（declarative vs code-capable）。

**信任审批（code plugin）**
- code-capable plugin 启用须用户显式审批（REST 显式 enable + `approve_code` 确认 / 或 ApprovalGate SSE），记 code-hash。
- 审批持久化（`.approved` marker 记 code-hash，或审批 store）；bootstrap 自动装载**仅** hash 匹配的已审批 code plugin；hash 不匹配 → `pending_approval` 惰性不 import。
- declarative plugin 无须审批（数据）。

**注册**
- skill → SkillDiscovery `PLUGIN` source + provenance（名冲突拒载不覆盖）。
- **plugin 工具 → 中央 ToolRegistry**（`scan_and_register` plugin tool 模块；注册期 fail-fast 契约；名冲突拒载）；工具经 Policy/ApprovalGate 治理。
- hooks（lifecycle：on_load/on_unload）→ 受控调用（已审批 code plugin 才调）。
- behavior → fallback-fill 最低优先级 overlay，allowlist=`{KNOWLEDGE.md}`。

**代码加载**
- `importlib` 加载已审批 code plugin 的模块（`tools.py`/`hooks.py`/`extensions/`）；隔离 try/except；import 失败拒载降级。

**toggle**
- `.disabled` marker（存在=禁用）。declarative toggle 自由；code plugin 从禁用→启用须（重新）审批。

**威胁扫描（声明式制品）**
- manifest + SKILL.md + KNOWLEDGE.md 过 `ContentThreatScanService.scan(scope=MEMORY)`；命中 BLOCK 拒载。scanner 异常 fail-open。

**watchdog 热重载**
- 加 `watchdog` 依赖 + 后台 observer 监听 `plugins_dir` + debounce；declarative 变更自动 refresh；**code/code-hash 变更 → plugin 转 `pending_approval`（不自动 reload 执行）+ 通知用户**。

**git 安装/更新**
- `asyncio.create_subprocess_exec` git clone（安装）/ pull（更新）；provenance（repo+commit）；更新若改 code → re-approval；网络/git 错误降级。

**REST**：`GET /api/plugins`（列表+状态+审批态+来源）/ `GET /{name}` / `POST /{name}/toggle` / `POST /{name}/approve`（code plugin 审批，记 hash）/ `DELETE /{name}`（仅 user plugins）/ `POST /refresh` / `POST /install`（git url）。front-door protected。

**基础设施**：`plugins_dir` DI；独立 bootstrap 段 7.5；shutdown 清 watchdog observer；审计事件；0 regression + e2e（含坏 plugin 降级 + code plugin 审批门控 + 未审批代码不执行）。

### 2.2 Out of Scope

| 排除项 | 归属 | 理由 |
|--------|------|------|
| **沙箱 / 子进程隔离 / 容器** | 后续加固（v0.2+）| 用户"v0.1 做不完沙箱"；任务"后续加固方向"；v0.1 = trust-on-install + 审批门控 |
| **代码签名 / 来源信任链** | 后续加固 | 同上；v0.1 = provenance 展示 + code-hash 审批 |
| **channel-as-plugin**（ChannelAdapter）| 扩展点 / 推迟 | 任务"channel-as-plugin 留后续"；H1 binding 收敛复杂度；handoff 交接 |
| **Companion** | M7 | 决策点 3 拍板；F106 仅装载基础设施 |
| **per-project / per-agent plugin scoping** | v0.2 | v0.1 全局 user-level（对齐 skill USER tier + 单用户）|
| **capability 作用域权限授予**（细粒度"允许此 plugin 用工具 X"）| 后续加固 | v0.1 = 整体启用审批；plugin 工具仍走 Policy |
| **plugin 市场 / 多用户 / 团队共享** | 退出（Blueprint §0 锁单用户）| — |
| **plugin 改 Policy 决策函数** | 不做（#10）| plugin 工具走 Policy，不改 Policy |
| Agent 协作模型 H1/H2/H3 改动 | 不做 | plugin 是 surface 装载 |

---

## 3. 关键决策点（Decision Points）

### DP-1 ★：Model B 代码可执行 + 两层信任（用户拍板）

plugin 分 **declarative**（数据，自由装）vs **code-capable**（含 .py，**启用须审批 + code-hash 绑定**）。信任模型见 §0.2。核心安全属性：**代码执行前必经用户显式审批；审批绑 code-hash；代码变更强制 re-approval**（闭合 watchdog/git 的"换码"洞）。

### DP-2：plugin skill → SkillDiscovery `PLUGIN` source，名冲突拒载（不覆盖）

同 v0.1 既定：扩 `SkillDiscovery.scan(plugin_skill_dirs=...)`，`source=PLUGIN` + `provenance`；plugin skill 与 builtin/user/project 同名 → 拒载该 skill（不覆盖，防劫持内置名）+ 审计。

### DP-3 ⟲：plugin 工具 → 专用 load path（**不** reuse `scan_and_register`，review H1/H2/H3）

**实测纠正**：`scan_and_register`（tool_registry.py:305）①调 `exec_module()` 执行模块代码（与"未审批不执行"矛盾）；②`registry` 参数是 no-op 语义标记，写**全局 `_REGISTRY` 单例**；③模块名无 namespace；④`register()` 是**覆盖**语义（:124）无冲突拒绝。故 **MUST NOT reuse**。

专用 plugin tool-load path（仅对 ENABLED+hash 匹配 plugin）：
- `importlib` 以 **namespaced 模块名** `octoagent_plugins.<name>.*` 加载（避免 sys.modules 撞名污染兄弟 plugin，H3）。
- 收集 plugin 声明的 `ToolEntry` 进 **staging set**，**先预检名冲突**（与现有 registry + 本 plugin 内）→ 冲突拒该工具 + 审计；再**事务性** `registry.register()` 提交；中途失败 **回滚已注册工具 + `sys.modules.pop`**（防半注册留孤儿工具，H3/M4）。
- 注册后工具的**调用**走 Policy + side-effect 走 ApprovalGate（#10）+ 注册期 WriteResult 契约（#3）——但仅对"plugin 经注册工具表达的 side-effect"成立（§0.2 控制 5 诚实边界）。

### DP-4：code plugin 启用审批（#4 两阶段，绑 code-hash）

启用 code plugin = 高风险 side-effect（运行任意代码）→ **两阶段**：发现（plan）→ 用户显式审批启用（gate）→ import+注册+执行（execute）。审批经 `POST /api/plugins/{name}/approve`（显式）或 ApprovalGate SSE。审批记 `code_hash`（plugin 内所有 .py 内容的稳定 hash）。bootstrap 自动重载**仅** hash 匹配的已审批 plugin（无须每次 re-prompt）；hash 不匹配 → `pending_approval`。**这使任意代码执行兼容 #4/#7/#9**（用户授权而非 Agent/LLM 自决）。

### DP-5：fail-fast 注册器 vs 单 plugin 降级（F108b 二分）

注册器自身不变量违反 → 构造期抛；**单 plugin 失败（manifest/制品/扫描/import/注册/名冲突）→ try/except 隔离 + `PLUGIN_REJECTED(reason)` + 继续**（#6）。复用 F105/SkillDiscovery 降级范式。

### DP-6：watchdog 热重载 + code 变更 re-approval（用户拍板）

加 `watchdog` 依赖 + 后台 `Observer` 监听 `plugins_dir` + debounce 合并事件。**declarative 变更**（SKILL.md/KNOWLEDGE.md/manifest 非 code）→ 自动 refresh 生效。**code 变更**（任一 hash 文件改 → code_hash 变）→ plugin 转 `pending_approval`。**不照搬 Agent Zero 盲目 purge_namespace reload**。**race 闭合（review H9/M4）**：
- 检测到 code plugin 变更 → 先 **deregister 其工具 + `sys.modules` evict 其模块**（在 dispatcher 同一把锁下，FR-4.4），**再**转 `pending_approval`——确保旧 handler 不被新调用派发、新代码不自动 import 执行。
- code_hash **仅在 quiesced tree 上算**（debounce 至无写 N ms / git plugin 用 `git rev-parse HEAD`），不信任半写瞬时 hash。
- **防 reload loop**：observer **忽略 loader 自身写** + **忽略 plugin 对自身目录的写**（plugin on_load 写文件触发循环）+ 每窗口最大事件数自限流。
- behavior overlay 刷新独立 debounce（防 plugin churn `KNOWLEDGE.md` 每 turn 失效缓存 DoS）。
- hermetic e2e：observer 可关（DI flag）。

### DP-7 ⟲：git 安装/更新 + 硬化（用户拍板；review H8/L1/L2）

`POST /api/plugins/install {repo_url}` → `git clone`；`POST /{name}/update` → `git pull`。`asyncio.create_subprocess_exec`（不引 GitPython）。**安全硬化（对齐 MCP installer 范式，缺失即 RCE）**：
- **repo_url 校验**：scheme allowlist 仅 `https://` / `git@host:`（SSH）；**禁** `ext::`/`fd::`/`file://`（`ext::` transport = clone 即执行任意 shell，H8）+ 禁 `--` 前缀值；git 命令用 `--` 终止符隔离 URL。
- **git 配置加固**：`-c protocol.ext.allow=never -c core.hooksPath=/dev/null -c core.fsmonitor=false`（禁 ext transport + 禁仓库 hooks + 禁 fsmonitor）。
- **env scrub**：复用 `_build_safe_env` 范式剥离宿主 secrets。
- **clone target 容纳**：clone 进 temp dir → 校验（无 symlink-`.git`、tree 不逃逸）→ move 进 `plugins_dir/<name>`（`_ensure_path_within` + kebab name 校验，**不** clone-over-existing）。
- **provenance**：repo+commit 从**实际 `.git`** 读（非 manifest 自报 `repo:`，L1）；audit-only 不验签（v0.2 签名）。
- **clone 的 code plugin 默认 `pending_approval`**（git 来源不自动信任）；`update` 改 code_hash → re-approval。git 不可用/网络/非法 repo → 降级（审计 + 不破坏现有 plugin）。

### DP-8：plugin 全局 user-level（per-project/agent 推 v0.2）

### DP-9 ⟲：装载期威胁扫描（声明式制品，正确 API + fail 模式拆分，review H4）

**实测纠正**：`ContentThreatScanService` **无** `scan(scope=MEMORY)` 方法——实为 `scan_memory(content) -> ThreatScanResult`（**同步**，查 `.blocked`）。故：manifest + SKILL.md + KNOWLEDGE.md 经 `scan_memory(content)`，`result.blocked` → 拒载（`reason=threat_flagged`）。**fail 模式拆分**：
- scanner **抛 Python 异常**（引擎 bug）→ **fail-open**（继续装 + `scanner_skipped=true` + warning，不因 scanner bug 全失效）。
- scanner 返回 **degraded BLOCK**（超 2MB `_MAX_SCAN_INPUT`，threat_scanner.py:579 fail-closed）→ **拒载该 plugin**（非 fail-open——超大不可扫制品按不可信处理）。

**.py 等代码不扫**（靠审批+provenance，不制造假信心）。不新增 pattern（复用冻结 17 条）。

### DP-10：H1 + 文件系统安全护栏

- **H1**：v0.1 plugin **不得注册 user-facing 渠道**（channel-as-plugin 推迟）；未来落地须复用 F105 `upsert_configured_binding` 的 `agent_profile_id != "" → raise` 守卫（binding 收敛主 Agent）。
- **文件系统**：name kebab 校验防 `../`；DELETE 仅 `plugins_dir` 内（`_ensure_path_within`）；behavior overlay 仅 allowlist + 不逃逸 plugin 目录。

### DP-11：behavior pack plugin = fallback-fill 最低优先级，allowlist = KNOWLEDGE.md only

仅当 system/project/agent/user 都缺该 file id 才用 plugin 填充（接入 filesystem 之后、default templates 之前）。allowlist=`{KNOWLEDGE.md}`（TOOLS.md 推 v0.2——工具策略干预风险 #9/#10；禁 IDENTITY/SOUL/HEARTBEAT/BOOTSTRAP/AGENTS/PROJECT/USER 护 H1/H2）。

---

## 4. User Scenarios & Testing（mandatory）

### User Story 1 — 装声明式 plugin，主 Agent 用其 skill（Priority: P1）

declarative plugin（`plugin.yaml` + `skills/*/SKILL.md` + 可选 `behavior/KNOWLEDGE.md`，无 .py）放进 plugins_dir。

**Independent Test**: `plugins_dir` DI 指 tmp，放合法 declarative plugin，发现+注册，断言 skill 进 SkillDiscovery（source=PLUGIN）+ 无审批要求 + 写 `PLUGIN_LOADED`。

**Acceptance Scenarios**:
1. **Given** 合法 declarative plugin，**When** 发现+注册，**Then** skill 进 SkillDiscovery（`source=PLUGIN`/provenance），`skills list` 可见，**无审批**（declarative），写 `PLUGIN_LOADED`。
2. **Given** 已注册，**When** `skills load`，**Then** 拿到逐字 body。
3. **Given** `provides.skills` 子目录缺 SKILL.md，**When** 注册，**Then** 拒载（`reason=missing_artifact`），其他不受影响。

### User Story 2 — code-capable plugin 启用须审批，未审批代码不执行（Priority: P1）

plugin 含 `tools.py`（注册新工具）+ `provides.tools`。

**Why this priority**: Model B 信任模型核心——任意代码执行的门控（§0.2）。

**Independent Test**: 放含 tools.py 的 plugin，断言未审批前代码不 import、工具不注册、状态 `pending_approval`；审批后 import + 工具进 ToolRegistry + code_hash 记录。

**Acceptance Scenarios**:
1. **Given** 含 tools.py 的 code plugin 首次发现，**When** 装载，**Then** 状态 `pending_approval`，**tools.py 未 import**（无副作用），工具**未**注册，写 `PLUGIN_LOADED(state=pending_approval, capabilities=[tools])`。
2. **Given** `pending_approval` 的 code plugin，**When** 用户 `POST /api/plugins/{name}/approve`，**Then** tools.py import，新工具注册进中央 ToolRegistry，状态 `enabled`，审批记 code_hash，写 `PLUGIN_APPROVED`。
3. **Given** 已审批 enabled 的 plugin 工具，**When** 主 Agent 调该**注册工具**触发 side-effect，**Then** 该工具调用走 Policy 决策 + ApprovalGate（cooperative plugin 经注册工具表达的 side-effect 受治理；恶意代码绕过不在 v0.1 可证范围，§0.3）。
4. **Given** plugin 的 tool 名与现有工具冲突，**When** 注册，**Then** 拒该工具（`reason=name_collision`）不覆盖 + 审计。
5. **Given** code plugin import 抛异常，**When** 启用，**Then** 隔离拒载（`reason=import_error`）+ 审计，gateway 与其他 plugin 不受影响（#6）。

### User Story 3 — 坏 plugin 隔离降级，gateway 正常（Priority: P1）

混装坏（manifest 错/威胁命中/import 错）+ 好 plugin。

**Acceptance Scenarios**:
1. **Given** plugin A manifest 非法 YAML、B 合法，**When** 发现，**Then** A 拒（`manifest_invalid`）、B 成功、bootstrap 不抛、gateway 正常。
2. **Given** plugin C 的 SKILL.md 含注入 payload，**When** 装载期扫描，**Then** C 拒（`threat_flagged`），审计无原文。
3. **Given** plugin D 的 skill 名与内置同名，**When** 注册，**Then** D 该 skill 拒（`name_collision`），内置不被覆盖。

### User Story 4 — watchdog 热重载（声明式自动 / code 变更 re-approval）（Priority: P2）

**Acceptance Scenarios**:
1. **Given** observer 运行，declarative plugin 的 KNOWLEDGE.md 被改，**When** watchdog 检测，**Then** 自动 refresh，新内容生效，无须审批。
2. **Given** 已审批 enabled 的 code plugin 的 tools.py 被改（code_hash 变），**When** watchdog 检测，**Then** plugin 转 `pending_approval`，**旧模块不再用于新调用、新代码不自动执行**，写 `PLUGIN_CODE_CHANGED` + 通知用户重新审批。

### User Story 5 — git 安装/更新（Priority: P2）

**Acceptance Scenarios**:
1. **Given** 用户 `POST /api/plugins/install {repo_url}`，**When** git clone 成功，**Then** plugin 落 plugins_dir，declarative 直接可用 / code plugin 默认 `pending_approval`（git 来源不自动信任），provenance 记 repo+commit。
2. **Given** 已审批 code plugin，**When** `update` git pull 改了 .py，**Then** code_hash 变 → re-approval（不自动跑新代码）。
3. **Given** git clone 网络失败，**When** install，**Then** 降级（4xx + 审计），现有 plugin 不受影响。

### User Story 6 — toggle + REST 列举/卸载（Priority: P2）

**Acceptance Scenarios**:
1. **Given** declarative plugin enabled，**When** toggle 禁用，**Then** skill 移除 + `.disabled` 落盘跨重启 + `PLUGIN_TOGGLED`。
2. **Given** code plugin 从禁用→启用，**When** toggle enable，**Then** 须（重新）审批才 import 执行（禁用不等于已审批）。
3. **Given** user plugin，**When** `DELETE`，**Then** 仅删 plugins_dir 内目录 + 移除 skill/工具/behavior + `PLUGIN_REMOVED`；越界→403/不存在→404。

### Edge Cases

- `plugins_dir` 不存在 → mkdir parents，正常启动。
- 无 `plugin.yaml` 的目录 → 跳过（非 plugin）。
- name 与目录名不一致 / 非 kebab / 含 `..` → 拒（`name_mismatch`/`name_invalid`）。
- code plugin 已审批但 .py 被手改（hash 变）→ bootstrap 时 `pending_approval`（不自动跑改动代码）。
- 威胁扫描 scanner 异常 → fail-open + `scanner_skipped`（DP-9）。
- ThreatScanner 命中 .py？→ **不扫 .py**（DP-9，代码靠审批）；只扫声明式。
- EventStore/ApprovalGate 不可用 → 降级（code plugin 无法审批则保持 `pending_approval`，不静默执行；declarative 正常，#6）。
- watchdog observer 启动失败 → 降级（无热重载，仍可手动 refresh，gateway 正常，#6）。
- git 不可用（无 git 二进制）→ install/update 4xx 降级，本地 plugin 不受影响。
- refresh 并发 / toggle+refresh 竞态 → `PluginRegistry` `asyncio.Lock` 串行 + SkillDiscovery 原子缓存替换。
- plugin import 有顶层副作用（网络/写盘）→ 仅已审批才 import（控制 2/7）；import 抛错隔离。

---

## 5. Requirements（mandatory）

### Functional Requirements

**FR-1 发现 + manifest + 能力分类**
- **FR-1.1**: MUST 扫 `plugins_dir`（默认 `~/.octoagent/plugins/`）一级子目录，含 `plugin.yaml` 者为候选；无则跳过。
- **FR-1.2**: MUST 定义 `PluginManifest`（`name/version/description/author?/repo?/provides{skills[]/behavior[]/tools[]/hooks?/extensions[]}`）；未知字段宽容。
- **FR-1.3**: manifest 校验 MUST：`name` kebab 且与目录名一致；`provides.skills` 子目录含 SKILL.md；`provides.behavior` ∈ allowlist；`provides.tools` 指向存在的 .py。不满足 → 拒载 + 审计。
- **FR-1.4**（能力分类，纯 stat，review H7）: 分类 MUST 纯文件系统 stat + 文本读，**绝不 import / `__import__` / 加 plugin 路径到 sys.path**。`code-capable` 触发 = 目录含任一 `*.py/*.pyc/*.so/*.dylib/*.pyd/*.pyx/*.pth/conftest.py/setup.py/pyproject.toml`（基于实际文件非 manifest 自报）；否则 `declarative`。declarative plugin 含上述任一 → 拒载或重分类为 code-capable。
- **FR-1.5**: `plugins_dir` MUST 为 OctoHarness DI（None=生产，非 None=隔离 tmp）；不存在 MUST mkdir。

**FR-2 信任审批（code plugin）**
- **FR-2.1**（惰性不执行，review H1）: code-capable plugin 代码 MUST NOT 被 import/`exec_module`/`scan_and_register`/任何 sys.path 加入，**除非**该 plugin 处于 ENABLED 态且 code_hash 匹配审批记录。未审批 = `pending_approval`（发现+列出但不 import）。MUST 补契约测试：`pending_approval` plugin 的模块名**不出现在 `sys.modules`**。
- **FR-2.2**（显式 human-initiated 审批，review M9）: 启用 code plugin MUST 经用户**独立 HTTP 请求**（`POST /api/plugins/{name}/approve` from UI，或 ApprovalGate SSE 决策）；MUST NOT 由 LLM/Agent 自决（#9），MUST NOT 是同轮可填的 flag（避 `behavior.write_file confirmed` 式自批）。
- **FR-2.3**（整树 code-hash 绑定，review H6）: 审批 MUST 记录 plugin **整个目录树**（全文件排序 path+content sha256，**非仅 .py**）的稳定 `code_hash`；审批持久化（跨重启）。bootstrap MUST 仅自动 import hash 匹配的已审批 plugin；hash 不匹配 → `pending_approval`。残余（runtime-fetch/eval）见 §0.3。
- **FR-2.4**（两阶段 #4）: code 执行是高风险 side-effect，MUST 走发现→审批→执行两阶段；审批=execute 授权点，写 `PLUGIN_APPROVED`。
- **FR-2.5**: declarative plugin MUST NOT 要求审批（数据，过威胁扫描即可）。

**FR-3 注册（skill / 工具 / hooks / behavior）**
- **FR-3.1**（skill）: plugin skill MUST 注册进 SkillDiscovery（`SkillSource.PLUGIN` + `provenance`，扩 `scan(plugin_skill_dirs=)`，`SkillMdEntry.provenance: str|None`）；与 builtin/user/project 同名 → 拒该 skill（不覆盖）+ 审计；plugin 间同名先注册胜。
- **FR-3.2**（工具，专用 path，review H1/H2/H3）: code plugin 工具 MUST 经**专用 load path**（**不** reuse `scan_and_register`，理由见 DP-3）：namespaced 模块 `octoagent_plugins.<name>.*` + staging 预检名冲突（冲突拒该工具不覆盖 + 审计）+ 事务性 `registry.register()`（半失败回滚 + `sys.modules.pop`）+ 注册期 fail-fast WriteResult 契约（#3）。注册后工具的**注册工具调用** MUST 走 Policy + ApprovalGate（#10）。
- **FR-3.3**（Policy 诚实边界，review H5/M6）: plugin 经注册工具表达的 side-effect MUST 走 Policy；但 plugin MUST 被如实记为"已审批=进程内任意代码，**可** monkeypatch Policy/Registry/直接 syscall——Policy 治理是 cooperative-only 契约，**非**对恶意已审批代码的强制边界"（§0.3）。契约测试只证**合作型** plugin 经注册工具走 Policy，不证恶意代码不可绕（不可证）。
- **FR-3.4**（hooks）: plugin `hooks.py` 的 lifecycle hook（on_load/on_unload）MUST 仅在已审批 code plugin 时调用；hook 抛错隔离不拖垮。
- **FR-3.5**（behavior）: plugin behavior fallback-fill 最低优先级（仅现有源缺该 file id 时填充）；allowlist=`{KNOWLEDGE.md}`（禁其余护 H1/H2）；越界 → 拒该项 + 审计；MUST NOT 逃逸 plugin 目录 / 绕过 `_PROFILE_ALLOWLIST`。

**FR-4 代码加载 + 降级隔离**
- **FR-4.1**（importlib）: 已审批 code plugin 模块 MUST 经 `importlib` 加载，命名空间隔离（避免与系统模块冲突）。
- **FR-4.2**（#6 降级 + 留毒清理，review M4）: 单 plugin 任一步失败（manifest/制品/扫描/审批缺失/import/注册/名冲突）MUST try/except 隔离 + 写 `PLUGIN_REJECTED(reason)` + 继续；MUST NOT 拖垮 bootstrap/gateway。**import 失败 MUST `sys.modules.pop` 半初始化模块**（防后续 import 返回坏模块）；plugin import 期注册的 atexit/非 daemon 线程风险 MUST 记入 residual（v0.1 无法强制阻止，§0.3）。
- **FR-4.3**（fail-fast 二分）: plugin_registry 自身不变量违反 MAY 构造期抛；单 plugin 失败必降级。
- **FR-4.4**（并发原子）: discover/register/approve/toggle/refresh MUST 经 `asyncio.Lock` 串行；refresh 复用 SkillDiscovery 原子缓存替换。

**FR-5 威胁扫描（声明式制品）**
- **FR-5.1**（正确 API，review H4）: manifest + SKILL.md + KNOWLEDGE.md MUST 经 `ContentThreatScanService.scan_memory(content) -> ThreatScanResult`（同步，不直接 import scan）；`result.blocked` → 拒载（`threat_flagged`）。
- **FR-5.2**: MUST NOT 扫描代码（.py/.so/...）做恶意检测（DP-9；代码靠审批+provenance）；MUST NOT 新增 ThreatScanner pattern。
- **FR-5.3**（fail 模式拆分，review H4）: scanner **抛 Python 异常**（引擎 bug）MUST fail-open（继续装 + `scanner_skipped=true` + warning）；scanner 返回 **degraded BLOCK**（超 2MB `_MAX_SCAN_INPUT`，fail-closed）MUST 拒载该 plugin（非 fail-open）。审计 payload MUST NOT 含原文。

**FR-6 watchdog 热重载**
- **FR-6.1**: MUST 加 `watchdog` 依赖 + 后台 Observer 监听 `plugins_dir` + debounce 合并事件；observer 可经 DI flag 关（hermetic e2e）。
- **FR-6.2**（declarative 自动）: declarative 制品变更（SKILL.md/KNOWLEDGE.md/manifest 非 code）MUST 自动 refresh 生效。
- **FR-6.3**（code re-approval）: code/code_hash 变更 MUST 使 plugin 转 `pending_approval`——**旧模块停止用于新调用、新代码 MUST NOT 自动 import/执行**，写 `PLUGIN_CODE_CHANGED` + 通知用户重新审批。MUST NOT 盲目 reload（闭合 §0.2 控制 7）。
- **FR-6.4**: observer 启动失败 MUST 降级（无热重载、手动 refresh 仍可、gateway 正常，#6）；shutdown MUST 停 observer。

**FR-7 git 安装/更新**
- **FR-7.1**: `POST /api/plugins/install {repo_url}` MUST 经 `asyncio.create_subprocess_exec` git clone 进 plugins_dir；provenance（repo+commit）MUST 从实际 `.git` 读（非 manifest 自报，review L1）。
- **FR-7.2**: clone 的 code plugin MUST 默认 `pending_approval`（git 来源不自动信任）；declarative 直接可用。
- **FR-7.3**: `update`（git pull）若改 code_hash MUST 触发 re-approval（FR-2.3）。
- **FR-7.4**: git 不可用 / 网络失败 / 非法 repo MUST 降级（4xx + 审计），现有 plugin 不受影响。
- **FR-7.5**（硬化 MUST，review H8/L2）: ①`repo_url` scheme allowlist 仅 `https://`/`git@host:`，**禁** `ext::`/`fd::`/`file://` + `--` 前缀值，git 用 `--` 终止符；②git 跑 `-c protocol.ext.allow=never -c core.hooksPath=/dev/null -c core.fsmonitor=false` + scrub env；③clone 进 temp → 校验（无 symlink-`.git`、不逃逸）→ move 进 `plugins_dir/<name>`（`_ensure_path_within` + kebab name，不 clone-over-existing）。

**FR-8 REST API**
- **FR-8.1**: `GET /api/plugins` → 列表（name/version/description/state[enabled/disabled/pending_approval/rejected]/capability[declarative/code]/source/provides/reject_reason?/scanner_skipped?），200。
- **FR-8.2**: `GET /{name}` → 详情，200；不存在 404。
- **FR-8.3**: `POST /{name}/approve`（code plugin）→ 记 code_hash + import + 注册 + 写 `PLUGIN_APPROVED`，200；非 code plugin → 4xx；不存在 404。
- **FR-8.4**: `POST /{name}/toggle {enabled}` → 切换 + refresh + `PLUGIN_TOGGLED`，200；code plugin 启用须已审批+hash 匹配否则转 `pending_approval`。
- **FR-8.5**: `DELETE /{name}` → 仅 plugins_dir 内目录 + 移除 skill/工具/behavior + `PLUGIN_REMOVED`，204；越界 403/不存在 404。
- **FR-8.6**: `POST /refresh` → 重扫原子更新，200 `{loaded, rejected, pending, total}`。
- **FR-8.7**: `POST /install {repo_url}` → git clone（FR-7），201/4xx。
- **FR-8.8**: 全部路由 front-door protected。
- **FR-8.9**（补 orphan endpoint，review M8）: `POST /{name}/update` → git pull（FR-7.3）+ code_hash 变则 re-approval，200/4xx；非 git plugin → 4xx。

**FR-9 审计事件**
- **FR-9.1**: MUST 新增 EventType `PLUGIN_LOADED` / `PLUGIN_REJECTED` / `PLUGIN_APPROVED` / `PLUGIN_TOGGLED` / `PLUGIN_REMOVED` / `PLUGIN_CODE_CHANGED`。`PLUGIN_REJECTED.reason` 取 `PluginRejectedReason` StrEnum。
- **FR-9.2**: 各动作 MUST 写对应事件（payload: name/version/state/capability/reason/source/code_hash，无敏感原文）。
- **FR-9.3**: EventStore 不可用 MUST 降级。

**FR-10 bootstrap + 生命周期**
- **FR-10.1**（段 7.5）: plugin 发现+注册 MUST 接入独立 `_bootstrap_user_plugins` 段（段 7 capability_pack 之后、段 9 executors 之前）；bootstrap 仅自动 import hash 匹配的已审批 code plugin。
- **FR-10.2**: bootstrap 整段 try/except（`app.state.plugin_registry=None` 降级），plugin 子系统不可用 MUST NOT 拖垮 gateway。
- **FR-10.3**: shutdown MUST 停 watchdog observer + 调已加载 plugin 的 on_unload hook（隔离）。

**FR-11 H1/H2/H3 + 0 regression**
- **FR-11.1**（H1，诚实，review M7）: 系统 MUST NOT 提供 plugin 注册 user-facing 渠道的 **supported API**（channel-as-plugin 推迟）；declarative plugin 结构性无此面；code plugin **运行时无法强制**（进程内可直接调 PlatformRegistry/开监听）→ 靠 trust+audit，enforcement=v0.2 沙箱。未来 channel-as-plugin 落地须复用 F105 `upsert_configured_binding` H1 守卫。
- **FR-11.2**（H2/H3）: plugin MUST NOT 破坏 `_PROFILE_ALLOWLIST` / 引入新委托模式。
- **FR-11.3**: 全量回归 MUST 0 regression vs f3d8a267；`pytest -m e2e_smoke` 全过；新增 plugin e2e（坏 plugin 降级 + code 审批门控 + 未审批代码不执行 + watchdog code re-approval）。

### Key Entities

- **`PluginManifest`**（新 Pydantic）：`name/version/description/author?/repo?/provides{skills[]/behavior[]/tools[]/hooks?/extensions[]}`；未知字段宽容。
- **`PluginCapability`**（枚举）：`DECLARATIVE` | `CODE`（基于实际 .py 存在判定，FR-1.4）。
- **`PluginState`**（枚举）：`ENABLED` | `DISABLED` | `PENDING_APPROVAL` | `REJECTED`。
- **`PluginRejectedReason`**（StrEnum）：`manifest_invalid`/`name_mismatch`/`name_invalid`/`missing_artifact`/`name_collision`/`threat_flagged`/`behavior_not_allowed`/`import_error`/`approval_missing`/`path_escape`/`unknown`。
- **`PluginRecord` / `PluginListItem`**：name/version/state/capability/source/provides/code_hash?/reject_reason?/scanner_skipped?。
- **`PluginRegistry`**（新服务）：发现 + 校验 + 能力分类 + 威胁扫描 + 审批 + importlib 加载 + 注册（SkillDiscovery + ToolRegistry + behavior overlay）+ toggle + refresh + watchdog + git + 降级隔离；`asyncio.Lock` 串行。
- **`code_hash`**：plugin 全部 .py 内容的稳定 hash（审批绑定 + 变更检测）。
- **审批持久化**：`.approved` marker（记 code_hash）或审批 store；跨重启。
- **`SkillSource.PLUGIN`** + `SkillMdEntry.provenance: str|None`。
- **`.disabled` marker** / **`plugins_dir` DI** / **watchdog Observer**。
- **新 EventType ×6**：`PLUGIN_LOADED/REJECTED/APPROVED/TOGGLED/REMOVED/CODE_CHANGED`。
- **behavior overlay plugin 源**：fallback-fill 最低优先级、`{KNOWLEDGE.md}` 受限。

---

## 6. Success Criteria（mandatory）

- **SC-001**（declarative 发现+注册）: 合法 declarative plugin 100% 发现，skill 进 SkillDiscovery（PLUGIN source）+ 无审批 + `PLUGIN_LOADED`。
- **SC-002**（code 审批门控）: code plugin 未审批前 **代码零 import/零副作用、工具未注册、状态 `pending_approval`**；审批后 import+注册+`PLUGIN_APPROVED`；e2e 实证"未审批代码不执行"。
- **SC-003**（code-hash 闭合）: 已审批 plugin 的 .py 被改（git/watchdog/手改）→ code_hash 变 → 转 `pending_approval`、新代码不自动执行、`PLUGIN_CODE_CHANGED`；e2e 实证"换码不绕审批"。
- **SC-004**（plugin 工具走治理，诚实边界）: plugin **经注册工具表达的** side-effect 走 Policy + ApprovalGate（契约测试证合作型 plugin 路由正确）；**不**声称恶意已审批代码不可绕（进程内不可证，§0.3 residual）。
- **SC-005**（降级隔离 #6）: 坏 plugin（manifest/威胁/import 错）隔离拒载、好 plugin 成功、bootstrap 不抛、gateway 正常；e2e 实证。
- **SC-006**（名冲突安全）: plugin skill/工具与内置同名 → 不覆盖 + 拒 + 审计。
- **SC-007**（威胁拒载）: 含注入的声明式制品装载期拒载，审计无原文；.py 不被扫描（靠审批）。
- **SC-008**（watchdog）: declarative 改自动生效；code 改 re-approval（不自动跑新码）；observer 失败降级。
- **SC-009**（git）: clone 安装成功（code plugin 默认 pending_approval），update 改码 re-approval，git 失败降级。
- **SC-010**（toggle 持久 + behavior 安全）: `.disabled` 跨重启；behavior 仅 KNOWLEDGE.md + 最低优先级 + 非 allowlist 拒。
- **SC-011**（H1/H2/H3，诚实边界）: 无 **supported** plugin surface 绕过主 Agent（declarative 结构性保证；code plugin 靠 trust+audit，非运行时强制——enforcement=v0.2 沙箱）；`_PROFILE_ALLOWLIST` 不变；无新委托模式。
- **SC-012**（0 regression + hermetic）: 0 regression vs f3d8a267；e2e_smoke 全过；`plugins_dir` DI 隔离不碰宿主 `~/.octoagent`；REST 契约 + 坏 plugin 降级 e2e 全绿。

---

## 7. Constitution & 哲学合规

| 规则 | 合规说明 |
|------|----------|
| **#1 Durability First** | `.disabled` / `.approved`(code_hash) marker 落盘跨重启；plugin 状态文件系统 SoT |
| **#2 Everything is an Event** | `PLUGIN_LOADED/REJECTED/APPROVED/TOGGLED/REMOVED/CODE_CHANGED` 审计（FR-9）|
| **#3 Tools are Contracts** | plugin 工具经中央 ToolRegistry 注册期 fail-fast WriteResult 契约（FR-3.2）|
| **#4 Side-effect Two-Phase** | **code plugin 启用=运行任意代码=高风险 side-effect → 发现→用户审批→执行两阶段**（DP-4/FR-2.4）；非 Agent 自决。诚实：两阶段门控**首次执行**（enable 粒度），不门控已审批代码**之后做什么**（进程内无法，§0.3）|
| **#5 Least Privilege** | 审计存 reason/hash 不存原文；provenance；plugin 工具走 Policy 不放大权限 |
| **#6 Degrade Gracefully** | 单 plugin 隔离降级；watchdog/git/EventStore/ApprovalGate 不可用降级（FR-4.2/6.4/7.4/9.3/10.2）|
| **#7 User-in-Control** | code plugin 须用户显式审批；toggle/delete 用户可控；code 变更须重新审批 |
| **#8 Observability** | plugin 状态/审批/拒载原因经 `PLUGIN_*` 事件 + `GET /api/plugins` 可查 |
| **#9 Agent Autonomy** | declarative plugin=数据 LLM 自选；**code plugin 审批是用户动作非 LLM 自决**（不让 Agent 自批代码执行）|
| **#10 Policy-Driven Access** | plugin **经注册工具**的访问走统一 Policy（cooperative-only 契约，非对恶意已审批代码的边界，§0.3/FR-3.3）；威胁扫描经 `ContentThreatScanService` 单入口 |
| **H1 管家 mediated** | declarative plugin 结构性不绕过主 Agent；code plugin **靠 trust+audit 非运行时强制**（FR-11.1/SC-011）；channel-as-plugin 推迟，落地须复用 F105 H1 守卫 |
| **H2 完整对等** | plugin behavior 不破坏 `_PROFILE_ALLOWLIST`（FR-3.5/11.2）|
| **H3 委托** | plugin 不引入新委托模式（FR-11.2）|

---

## 8. 备注 / 风险 / 下游

- **后续加固方向（handoff）**：①子进程/容器沙箱（隔离 plugin 代码）；②代码签名 + 来源信任链；③capability 作用域权限（细粒度"允许此 plugin 用工具 X"而非整体启用）；④per-tool 审批。v0.1 = trust-on-install + 整体审批门控 + code-hash 闭合。
- **channel-as-plugin 扩展点（handoff）**：Model B 已开代码面，但 channel-as-plugin 仍推迟（任务"留后续" + H1 binding 收敛复杂度）。落地：plugin 提供 ChannelAdapter 类 → PlatformRegistry，binding 须收敛主 Agent（F105 `upsert_configured_binding` 守卫）。
- **Companion → M7**。
- **watchdog 依赖**：新增 pyproject 依赖（评估体积/平台兼容）；hermetic e2e 须可关 observer。
- **import 隔离风险**：plugin 模块 import 进主进程，顶层副作用风险——靠"仅已审批才 import"（FR-2.1）+ import try/except 隔离（FR-4.2）缓解；彻底隔离=沙箱（后续）。
- **OctoBench scorer**：若写 plugin bench task，新 EventType 加进 scorer 集（F114 先例，memory `project_threat_scanner_bench_coverage`）。
- **Blueprint 同步（MUST）**：新装载子系统 + 工具/能力面 + 安全模型，完成后同步 `docs/blueprint/`（module-design/milestones/安全模型）+ 新 `docs/codebase-architecture/plugin-loader.md`。

---

## 9. AC ↔ Test 绑定（SDD 强化规则）

| AC / FR | 目标 test |
|---------|----------|
| US1-AC1/AC2（declarative skill 注册无审批 + load body）| `apps/gateway/tests/.../test_plugin_registry.py::test_declarative_plugin_skill_no_approval` |
| US1-AC3（缺制品拒载）| `::test_missing_artifact_rejected` |
| US2-AC1/SC-002（未审批代码零 import/零注册）| `::test_code_plugin_pending_no_import` |
| US2-AC2（审批后 import+注册+code_hash）| `::test_approve_imports_and_registers_tools` |
| US2-AC3/SC-004（plugin 工具走 Policy+ApprovalGate）| `::test_plugin_tool_goes_through_policy` |
| US2-AC4（工具名冲突拒不覆盖）| `::test_plugin_tool_name_collision_rejected` |
| US2-AC5（import 错隔离降级）| `::test_import_error_isolated` |
| US3（坏 plugin 隔离 + 威胁拒 + 名冲突）| `::test_bad_plugins_isolated_good_loads` |
| US4-AC1（declarative 自动热重载）| `::test_watchdog_declarative_auto_refresh` |
| US4-AC2/SC-003（code 变更 re-approval 不自动执行）| `::test_watchdog_code_change_requires_reapproval` |
| US5-AC1/AC2（git install + update re-approval）| `apps/gateway/tests/.../test_plugin_git.py::test_install_and_update_reapproval` |
| US5-AC3（git 失败降级）| `::test_git_failure_degrades` |
| US6（toggle 持久 + code 启用须审批 + delete）| `::test_toggle_persist_and_delete` |
| FR-3.3/SC-004（plugin 不旁路/改 Policy）| `::test_plugin_cannot_bypass_policy` |
| FR-3.5（behavior allowlist + 路径穿越拒）| `::test_plugin_behavior_allowlist_and_path_traversal` |
| FR-5.1/5.3（声明式扫描经单入口 + fail-open）| `::test_declarative_scan_via_service_fail_open` |
| FR-5.2（.py 不被扫描）| `::test_py_not_threat_scanned` |
| FR-10.1（bootstrap 段序 + 仅自动装已审批 hash 匹配）| `apps/gateway/tests/e2e/test_plugin_bootstrap_e2e.py::test_bootstrap_order_and_approved_only` |
| SC-002/SC-003 e2e（审批门控 + 换码闭合，端到端）| `apps/gateway/tests/e2e/test_plugin_trust_e2e.py` |
| SC-012（0 regression + hermetic DI）| 全量 `pytest` + `pytest -m e2e_smoke` + `::test_plugins_dir_di_isolation` |

---

## 10. 交接 plan 的已知精度项

> 架构方向已拍板（Model B + watchdog + git）。plan 阶段带真实代码定稿：

1. **审批持久化形态**：`.approved`(记 code_hash) marker vs 审批 store（SQLite）；与 ApprovalGate（session-scoped）的关系——plugin 审批是持久的（非 session），可能需独立 store 或 marker。倾向 marker（文件系统 SoT，对齐 `.disabled`）。
2. **code_hash 算法**：plugin 内 .py 排序 + 内容 sha256 拼接；含/不含 manifest？（manifest 改不应失效 code 审批，除非改了 provides.tools）。
3. **importlib 加载机制**：模块命名空间（`octoagent_plugins.<name>.tools`）+ sys.modules 管理 + 卸载/reload（stale 标记）；与 ToolRegistry `scan_and_register` 的对接（plugin tool 目录作为额外 scan root）。
4. **plugin 工具注册落点**：复用 `scan_and_register(registry, plugin_tool_dir)` vs 独立路径；注册期 fail-fast 与单 plugin 降级的协调（一个坏工具不拖垮整个 registry）。
5. **watchdog observer 生命周期**：线程 + debounce 实现 + 与 asyncio.Lock/refresh 的线程安全对接 + hermetic DI flag；事件→code/declarative 分流判定。
6. **git subprocess 封装**：clone/pull 超时 + 错误码 + repo url 校验（防注入）+ commit 读取。
7. **behavior overlay 接入点**：`resolve_behavior_pack` source_chain 插入 + allowlist 收窄 + mtime 兼容。
8. **bootstrap 段 7.5 + DI 线**：与 capability_pack（SkillDiscovery）+ tool_registry（段 3）+ approval_gate 的依赖顺序。
9. **ApprovalGate vs 显式 REST approve**：v0.1 用显式 REST approve（简单，用户经 UI 主动）还是接 ApprovalGate SSE——倾向 REST approve 为主路径，ApprovalGate 为可选（plan 定）。

---

## 11. Codex Adversarial Review 闭环记录

**spec review round-1（双 panel：安全红队 + Constitution/arch，code-grounded）**：9 HIGH + 9 MED + 数 LOW，**全闭环**（详 [spec-review-r1.md](./spec-review-r1.md)）。关键：H1/H2 `scan_and_register` 执行代码+忽略 registry 参数→DP-3 专用 path；H4 `scan_memory` API 纠正；H5/M6 honesty（进程内无容纳）→§0.3；H6 整树 hash；H8 git 硬化；H9 race 闭合；分阶段 §13。

**实施 review（Phase A+B，双 panel：安全红队 + correctness，code-grounded）**：**0 HIGH**——信任模型 gate 被代码强制（未审批不 import airtight / 整树 hash / reconcile unload-then-rebuild 闭合换码）。MED-1（全局 register 篡改）+ M1（hooks）+ N1（失败审批 re-exec）+ N2（toggle 语义）**全修**；LOW-1/2/3 归档。0-regression 4098 passed。

**实施 review（Phase C watchdog+git，双 panel）**：红队实证抓出 **H-1（symlink 换码绕 code_hash 重审，latent 自 Phase A+B）→ 已修**（`validate_no_symlinks` 拒 + iter 跳 symlink 目录 + code_hash fold + loader 拒 symlink 模块）；L-1（watcher post-stop race）→ 已修（`_stopped` guard）；git 硬化/H9 watcher TOCTOU 实测 closed。N1/N2/N5 + 测试 gap 归档 handoff。0-regression 4130 passed。详 completion-report.md §3。

> Codex CLI OAuth 本会话不可用 → 由两独立对抗 agent panel 替代（F103c 先例）。合入前强烈建议另跑 Codex 复核（尤其 H-1 这类 latent 缝）。

---

## 12. GATE_DESIGN 决议（已拍板 2026-06-21）

- **DP-1**：✅ Model B 代码可执行（用户选）。信任模型 §0.2：trust-on-install + 审批门控 + code-hash 闭合 + provenance + 审计，**v0.1 无沙箱**。
- **DP-6**：✅ watchdog 自动热重载（用户选）；**code 变更 re-approval**（不盲目 reload）。
- **DP-7**：✅ v0.1 内置 git 安装/更新（用户选）；code 变更 re-approval。
- **driver 守界**（honor 任务原文，未上 gate）：channel-as-plugin 仍推迟（任务"留后续"）；沙箱/签名推后续加固（任务"后续加固方向" + 用户"做不完沙箱"）；behavior allowlist=KNOWLEDGE.md only；全局 user-level。

---

## 13. 实施分阶段（review 双 reviewer 独立收敛；全在 F106 下，非拆 Feature）

> 风险集中在 3 个独立硬问题（进程内代码执行+registry / watchdog 线程生命周期 / git 子进程），spec v0.2 把它们纠缠。按依赖排序 + 把任意代码风险隔离到 B/C 后的显式 review gate。**用户三选全保留，只排序不删。**

- **Phase A — declarative spine（最小安全切片，无代码执行/watchdog/git）**
  plugins_dir DI + 段7.5 bootstrap + `PluginManifest` + 纯 stat 能力分类（FR-1.4）+ declarative skill 注册（PLUGIN 最低优先级 + 冲突拒，DP-2）+ `scan_memory` 威胁扫描（FR-5）+ KNOWLEDGE.md fallback overlay（DP-11）+ `PLUGIN_LOADED/REJECTED` 事件 + REST list/get/toggle/delete/refresh + 降级隔离。**数据 only，结构性 H1-safe，零任意代码风险，安全可单独发布。** 闭 SC-001/005/006(skill)/007/010/011(decl)/012。
- **Phase B — code-capable + 审批 + 整树 code-hash（安全核心）**
  专用 plugin tool-load path（namespaced + 直接 register + staging 冲突拒 + 事务回滚，**非** scan_and_register，DP-3）+ 整树 code_hash（FR-2.3）+ `POST /approve`（human-initiated，FR-2.2）+ pending_approval 惰性不 import（FR-2.1）+ import 隔离 + hooks + §0.3 honest 限制。**dual-review 预算重点；Gate B 过 review 再 Phase C。** 闭 SC-002/004/006(tool)。
- **Phase C — watchdog + git（建于 B 的 code-hash）**
  watchdog observer（declarative 自动 + code 变更→pending_approval + race 闭合，DP-6）+ git 硬化 install/update（DP-7）+ `PLUGIN_CODE_CHANGED` + shutdown teardown。闭 SC-003/008/009。

**实施实况**：GATE_TASKS 用户拍板"本会话做 Phase A + B"，实施后用户"继续 Phase C" → **A + B + C 全本会话完成**。各阶段双 panel review 0 HIGH（Phase C 红队抓修 H-1 symlink RCE）。0-regression 4130 passed。仅 behavior overlay（FR-3.5）+ channel-as-plugin 扩展点推迟。
