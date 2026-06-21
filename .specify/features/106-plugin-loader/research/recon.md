# F106 User Plugin Loader — 块 A 实测侦察 + az-2 调研综合

> 状态：研究阶段产物（codebase-scan + 竞品源码深读）。本文件是 spec 的权威输入，spec 不得发明超出本文记录的范围。
> 侦察日期：2026-06-21（master f3d8a267）。repo 布局：`octoagent/` 前缀（`octoagent/packages/...`、`octoagent/apps/...`）。

---

## §0 一句话结论（驱动 spec 的核心判断）

**Agent Zero 的 plugin 执行任意 Python（`hooks.py` / `execute.py` / `extensions/`），而 OctoAgent 现有 skill 系统是纯声明式（`SKILL.md` = markdown 指令，无可执行代码）。** F106 v0.1 必须在两条路之间拍板（GATE_DESIGN 第一决策点）：

- **Model A（声明式 only，推荐 v0.1）**：plugin = `~/.octoagent/plugins/<name>/` + `plugin.yaml` manifest + 捆绑的 **声明式制品**（`SKILL.md` 技能指令 + behavior pack markdown 覆盖文件）。**无进程内可执行代码**。信任模型退化为"恶意 LLM 指令"（ThreatScanner + Policy + ApprovalGate 已有缓解），**不引入任意代码执行面**。
- **Model B（代码可执行，推迟 = 后续加固方向）**：plugin 可携带 Python（新工具 handler / `ChannelAdapter` 类 / hooks）。这正是 channel-as-plugin 扩展点 + Agent Zero 对等，**任务已显式推迟**（"channel-as-plugin 留后续"），需真信任模型（进程内执行风险 / import 隔离 / 签名 / provenance）。

任务原话支撑 Model A 为 v0.1：①"v0.1 聚焦 skill/behavior plugin，channel-as-plugin 留后续"；②安全要求是"明确 v0.1 信任模型 + 后续加固方向"（不是"做沙箱"）。

---

## §1 SkillDiscovery —— v0.1 在它之上升级，不从零

| 项 | 事实 | 文件:行 |
|----|------|---------|
| 核心类 | `SkillDiscovery`（内存缓存 `name -> SkillMdEntry`，`scan()` 原子替换缓存，`refresh()` = rescan） | `octoagent/packages/skills/src/octoagent/skills/discovery.py:105` |
| 三级目录 | BUILTIN(`{repo}/skills/`) < USER(`~/.octoagent/skills/`) < PROJECT(`{project}/skills/`)，扫描顺序低→高，同名高覆盖低 | `discovery.py:163`；路径在 `capability_pack.py:156` |
| 来源枚举 | `SkillSource` StrEnum：`BUILTIN/USER/PROJECT`（**F106 在此加 `PLUGIN`**） | `skill_models.py:16` |
| manifest | 文件名 `SKILL.md`，YAML frontmatter + markdown body。Pydantic `SkillMdEntry`，必填 `name`(kebab `^[a-z0-9]+(-[a-z0-9]+)*$`,≤64)/`description`(≤1024)；可选 version/author/tags/trigger_patterns/tools_required/metadata/resource_limits | `skill_models.py:31` |
| **声明式本质** | skill = markdown 指令 + `tools_required`（引用**已存在**的工具名）。**skill 不能引入新可执行工具**，只能用指令编排现有工具 | `discovery.py:363`（构造 entry，content=body） |
| REST | `GET/POST/DELETE /api/skills`；POST 写 USER tier + `discovery.refresh()`；DELETE 仅 USER tier（builtin/project → 403） | `octoagent/apps/gateway/src/octoagent/gateway/routes/skills.py` |
| 安全删除边界 | DELETE 仅 `source==USER`；`_ensure_path_within(user_dir)` 防穿越；name kebab 校验防 `../` | `routes/skills.py`（403 分支 + path guard） |
| 降级语义 | **graceful**：坏 SKILL.md（编码/无 frontmatter/YAML 错/缺必填/模型错）→ 记 warning + 跳过 + `total_skipped++`，继续扫描，**不 fail-fast** | `discovery.py:283`（`_parse_skill_file` 返回 None） |
| LLM 暴露 | 单个 `skills` 工具（list/load/unload action），**不**把每个 skill 注册为独立工具；session 级 `loaded_skill_names` | `packages/skills/.../tools.py`（`SkillsTool`） |
| toggle | **不存在**系统级 enable/disable；仅 session 级 load/unload（不跨重启） | — |
| 装配 | `CapabilityPackService` 构造 SkillDiscovery；`deps.py get_skill_discovery()` DI；bootstrap 段 7 `_bootstrap_capability_pack` | `capability_pack.py:154`、`octo_harness.py` |
| 测试 | `packages/skills/tests/test_skill_discovery.py`（override/skip/priority）、`apps/gateway/tests/test_skills_api.py` | — |

---

## §2 Behavior pack —— 当前纯项目内部，F106 引入全局 plugin 覆盖源

| 项 | 事实 | 文件:行 |
|----|------|---------|
| 模型 | `BehaviorPack`（pack_id hash / profile_id / scope / source_chain / files / layers / metadata） | `octoagent/packages/core/src/octoagent/core/models/behavior.py:98` |
| 9 文件 | AGENTS/USER/TOOLS/BOOTSTRAP（system-shared）+ PROJECT/KNOWLEDGE（project-shared）+ IDENTITY/SOUL/HEARTBEAT（agent-private） | `core/behavior_workspace/_types.py:7` |
| 磁盘布局 | `<root>/behavior/system/`、`<root>/behavior/agents/{slug}/`、`<root>/projects/{slug}/behavior/` | `behavior_workspace/paths.py:50` |
| allowlist | `_PROFILE_ALLOWLIST`：FULL(9)/WORKER(8,去 BOOTSTRAP)/MINIMAL(4) | `_types.py:94` |
| 解析 | `resolve_behavior_pack`（3 路：filesystem → metadata raw pack → default templates），session 级缓存 + mtime 失效 | `apps/gateway/.../services/agent_decision.py:180` |
| **外部安装路径** | **不存在**。当前只有 项目内 filesystem / metadata 内嵌 / 内置模板。F106 是首个"从外部装 behavior pack" | — |
| write 工具 | `behavior.write_file(file_id, content, confirmed=False)`；REVIEW_REQUIRED + not confirmed → 返回 skipped(proposal)；**confirmed 非 LLM 自填**，须显式设 | `services/builtin_tools/misc_tools.py:191` |
| 降级 | 多数 graceful（metadata 坏 → 静默跳下一路；resolve 失败 → warning + None 不阻断）；filesystem 读 fail-fast（异常上抛） | `agent_decision.py:247`、`agent_context.py:362` |
| 事件 | `BEHAVIOR_PACK_LOADED`（cache miss 时）/`BEHAVIOR_PACK_USED`（每 turn） | `behavior.py:290` |
| 缓存失效 | `invalidate_behavior_pack_cache(project_root)`（写后调用） | `misc_tools.py:284+` |

**H2 风险点**：plugin 提供的 behavior pack 是 **全局/USER scope**，但现有 behavior 覆盖是 project-internal + per-agent。plugin behavior 须作为**新的低优先级覆盖源**接入，且默认仅影响明确允许的文件集（见 §6 信任模型）。

---

## §3 F105 platform registry —— channel-as-plugin 是**扩展点**（v0.1 不做）

| 项 | 事实 | 文件:行 |
|----|------|---------|
| Registry | `PlatformRegistry.register(adapter)` fail-fast：非 Protocol→`TypeError`；platform_id 重复→`ValueError`；alias 冲突→`ValueError` | `octoagent/apps/gateway/src/octoagent/gateway/channels/registry.py:29` |
| Adapter | `ChannelAdapter` Protocol（`@runtime_checkable`）：`meta` / `inbound_router()->APIRouter\|None` / `notification_channel()` / `notify_task_result()` / `startup()`/`shutdown()` | `channels/adapter.py:55` |
| H1 不变量 | `ConversationBinding` UNIQUE(platform, account_id, conversation_id, project_id)；`agent_profile_id=""`=主 Agent；`upsert_runtime_binding` **签名不含** agent_profile_id；`upsert_configured_binding` 若 `agent_profile_id!=""` → **raise ValueError**（H1 构造性保证） | `core/models/conversation_binding.py`、`store.py:92/153` |
| 诚实边界 | registry 化：outbound/notification/lifecycle/route 挂载；per-platform：事件解析/验签/授权/config schema | `docs/codebase-architecture/platform-gateway.md §2` |
| **F105 handoff §5（逐字）** | "ChannelAdapter 是 plugin 形态的天然候选…plugin 提供 adapter 类 + config schema + route 描述，loader 注册进 PlatformRegistry；registry fail-fast 语义…天然适配 plugin 装载失败降级（Constitution #6：单 plugin 坏不拖垮 gateway）" | `.specify/features/105-platform-gateway-v01/handoff.md:42` |

**结论**：F106 v0.1 把 channel-as-plugin 记为 **handoff 扩展点**——因为 adapter 是可执行 Python 类（Model B），落在"后续加固方向"。registry fail-fast 语义是 F106 plugin_registry 降级设计的**直接范式来源**。

---

## §4 跨切面基础设施（F106 复用 / 缺口）

| 主题 | 事实（复用点 / 缺口） | 文件:行 |
|------|----------------------|---------|
| Registry fail-fast | `tool_registry.py:113` 注册期 `_enforce_write_result_contract`；`control_plane/_base.py:48` typed DI（9 service 缺一 `TypeError`）。**F108b W7 二分判断**：registry 构造期 fail-fast vs 外部资源降级 —— F106 直接套用："plugin_registry 注册器本身 fail-fast，但单个 plugin 装载失败 = 外部资源 → 隔离降级" | `tool_registry.py`、`_base.py` |
| 优雅降级 #6 | 范式：`try/except → log warning → set None/degraded → 继续`。实例：pipeline_registry、daily_routine、MCP、platform binding | `octo_harness.py:566/800/1374` |
| **watchdog 库** | **不是依赖**（pyproject 无 `watchdog`）。现有"watchdog"是 APScheduler 任务态监控（`services/watchdog/scanner.py`），**非文件监听** | `pyproject.toml`、`services/watchdog/scanner.py:35` |
| git 操作 | **无 GitPython**；范式 `asyncio.create_subprocess_exec`（mcp_installer 用于 npm） | `services/mcp_installer.py` |
| REST router | `APIRouter(prefix="/api/X")` → `app.include_router(..., dependencies=protected)`；`require_front_door_access` | `main.py:342`、`routes/skills.py:62` |
| ThreatScanner | `harness/threat_scanner.py`，`ScanScope.MEMORY`(17 冻结)/`CONTEXT`；**pattern 是 prompt-injection 文本检测，非代码安全**。`ContentThreatScanService` 单入口 | `threat_scanner.py:28`、`content_threat_scan.py` |
| `~/.octoagent/` | `Path.home()/".octoagent"`，有 DI 覆盖（`data_dir`/`mcp_servers_dir`/pipelines 用 `~/.octoagent/pipelines`）。**F106 需 `plugins_dir` DI 参数**（hermetic e2e 隔离） | `octo_harness.py:331/478/810` |
| bootstrap | 11 段 `octo_harness.bootstrap()`；plugin 发现宜插在段 7(capability)/8(mcp) 之后、段 9(executors) 之前；shutdown hook 范式 | `octo_harness.py:160` |

---

## §5 Agent Zero az-2 patterns（`helpers/plugins.py` 逐项）

| pattern | Agent Zero 实现 | F106 v0.1 取舍 |
|---------|----------------|----------------|
| manifest | `plugin.yaml`（`PluginMetadata`：name/title/description/version/settings_sections/per_project_config/per_agent_config/always_enabled） | ✅ 采用 `plugin.yaml`，字段适配 OctoAgent（声明 provides: skills[] / behavior[]） |
| plugin roots | user 优先：`usr/plugins/`、`plugins/`；目录约定（含 `plugin.yaml` 即 plugin），首 root 胜 | ✅ `~/.octoagent/plugins/<name>/`（user）+ 可选内置 |
| `.toggle` | **两文件**：`.toggle-0`(disabled)/`.toggle-1`(enabled)，按路径链决议；默认 enabled | ⚠️ v0.1 简化为**单 `.disabled` marker**（存在=禁用，缺省=启用）——更简单，无需 per-project/agent 链 |
| 删除安全 | `delete_plugin` 仅 `is_in_dir(custom_plugins_dir)`，"Only custom plugins can be deleted" | ✅ 完全对齐现有 skill USER-tier-only delete |
| **可执行代码** | `hooks.py`（uninstall/get_config 等生命周期 hook）/`execute.py`/`extensions/`；`.py` 变更 → `modules.purge_namespace` 重载 | ❌ **v0.1 不采用**（= Model B，推迟）。这是 az-2 与 OctoAgent 声明式哲学的最大分歧 |
| watchdog 热重载 | `watchdog.add_watchdog(patterns=["**/extensions/**/*", TOGGLE_FILE_PATTERN, HOOKS_SCRIPT])` + 缓存清除 + 前端 reload 通知 | ⚠️ 决策点：真 watchdog（新依赖+后台线程+debounce）vs 轻量（startup scan + `POST /refresh` 显式重载 + mtime） |
| git update | `git.get_repo_release_info` / `get_remote_commits_since_local`（commits_since_local / branch / remote） | ⚠️ 决策点：subprocess git clone/pull 纳入 v0.1 vs 推迟（v0.1 仅本地目录安装） |

---

## §6 v0.1 信任模型（Model A 下，spec 须明确）

Model A 下 plugin 制品是 **数据**（LLM 指令 markdown + behavior 文本），非可执行代码。风险面与缓解：

1. **恶意指令 / 间接 prompt injection**（plugin SKILL.md / behavior 文本诱导 LLM）→ 装载期过 `ThreatScanner`（`ScanScope.MEMORY` 或新 scope）扫 manifest + 制品内容；命中 BLOCK → 拒载该 plugin（隔离，不拖垮 gateway）。
2. **文件系统安全**（路径穿越 / 覆盖系统文件 / 越界删除）→ 复用 skill 的 `_ensure_path_within` + kebab name 校验 + USER-tier-only delete。plugin behavior 覆盖**仅允许写入 allowlist 文件集**，禁止逃逸出 plugin 目录。
3. **工具权限不被 plugin 放大**（Constitution #9/#10）→ plugin SKILL.md 的 `tools_required` 只能引用**已存在**工具；不能新增工具；工具访问仍走统一 Policy 决策；side-effect 仍走 ApprovalGate。plugin **不能**改 Policy。
4. **H1 不被绕过** → plugin 不得提供任何让平台直连用户、绕过主 Agent 的能力（channel-as-plugin 是 Model B，已推迟；v0.1 物理上无此面）。
5. **降级隔离（Constitution #6）** → 单 plugin manifest 错/制品坏/Protocol 不符 → fail-fast 拒载该 plugin + 记审计事件 + 继续启动，**不拖垮 gateway**（套 §4 registry 构造期 vs 外部资源降级二分）。

**后续加固方向（写进 handoff，非 v0.1）**：Model B 代码可执行 plugin 的真信任模型——import 隔离 / 子进程沙箱 / 签名与 provenance / capability 声明审批 / channel-as-plugin（adapter 类经 PlatformRegistry 注册，H1 约束 binding 收敛主 Agent）。

---

## §7 GATE_DESIGN 待用户拍板（产品/风险级）

1. **信任模型 A vs B**（最大）：v0.1 = 声明式 only（推荐，安全可发布、复用现有面、避开沙箱难题）vs 代码可执行（az-2 全保真，需真信任模型）。
2. **v0.1 范围裁剪**：watchdog 真热重载 + git update 是否真 v0.1，还是轻量化/推迟（任务 scope 列了，但是最复杂/最贵的两块；单用户个人 OS 下 startup scan + 显式 `POST /refresh` 可能足够 v0.1）。

driver（我）自决（不上 gate）：复用 SkillDiscovery 加 `PLUGIN` source / `plugin.yaml` schema / `~/.octoagent/plugins/` + `plugins_dir` DI / 单 `.disabled` marker / fail-fast 注册器 + 单 plugin 降级 / `/api/plugins` router / H1 与文件系统安全护栏。

---

## §8 H1/H2/H3 约束（不可违反，来自 agent-collaboration-philosophy.md §2.4）

- **H1**：plugin 不得让任何渠道/Worker/Subagent 绕过主 Agent 直接对用户说话。channel-as-plugin（Model B）若未来落地，binding 必须收敛到单一主 Agent（`agent_profile_id=""`），复用 F105 `upsert_configured_binding` 的 H1 raise 守卫。
- **H2**：plugin behavior pack 作为低优先级覆盖源接入，不破坏 Worker/main 的完整上下文栈对等；不得让 plugin 注入绕过 `_PROFILE_ALLOWLIST`。
- **H3**：plugin 不引入新委托模式；委托仍走现有 SubagentDelegation / A2A WorkerDelegation。
