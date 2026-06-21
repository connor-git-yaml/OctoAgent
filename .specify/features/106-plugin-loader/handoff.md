# F106 Handoff —— Phase C + 余项 + 扩展点

> Phase A + B 核心已交付（completion-report.md）。本文交接后续会话需做的 + 扩展点。

## §1 Phase C（watchdog + git，下一会话，独立 dual-review 0 HIGH）

**watchdog（FR-6，DP-6）**：
- 加 `watchdog` pyproject 依赖 + 后台 `Observer` 监听 `plugins_dir` + debounce。
- declarative 变更 → 自动 `registry.refresh()`；**code/code_hash 变更 → 转 pending_approval**（reconcile 已有逻辑：`_unload_all_code` + hash 不匹配 → pending；watchdog 只需触发 refresh，换码闭合已在 Phase B 实现）。
- emit `PLUGIN_CODE_CHANGED`（EventType 已定义）。
- **race 闭合已在 Phase B 基础具备**：reconcile 在 `asyncio.Lock` 下 unload-then-rebuild。watchdog 须经同一 registry 锁触发 refresh（observer 线程 → asyncio loop 桥接，如 `loop.call_soon_threadsafe` + `asyncio.run_coroutine_threadsafe(registry.refresh())`）。
- 防 reload loop：忽略 loader 自身写 + plugin 自写循环 + 每窗口最大事件数。
- hermetic：observer 经 DI flag 关（e2e tmp plugins_dir）。shutdown 停 observer（`PluginRegistry.shutdown` 已 unload code；加 observer.stop）。

**git（FR-7，DP-7，**含 spec-review H8 硬化，缺失即 RCE**）**：
- `POST /api/plugins/install {repo_url}` + `POST /{name}/update`，`asyncio.create_subprocess_exec`（不引 GitPython）。
- **硬化 MUST**（对齐 mcp_installer 范式）：①scheme allowlist `https://`/`git@host:`，**禁 `ext::`/`fd::`/`file://`**（ext:: = clone 即 RCE）+ `--` 终止符；②`-c protocol.ext.allow=never -c core.hooksPath=/dev/null -c core.fsmonitor=false` + scrub env；③clone temp → 校验（无 symlink-`.git`、不逃逸）→ move（`_ensure_within` + kebab name，不 clone-over-existing）。
- clone 的 code plugin 默认 `pending_approval`；update 改 code_hash → re-approval（reconcile 已自动）。provenance 从实际 `.git` 读（非 manifest）。

## §2 behavior overlay 余项（FR-3.5/US4，Phase A.5）

- `provides.behavior`（仅 `KNOWLEDGE.md`）已**校验 + 威胁扫描**，未接 overlay。
- 接入点：`agent_decision.resolve_behavior_pack`（`agent_decision.py:180`）source_chain，**fallback-fill**（仅现有 system/project/agent/user 源缺 KNOWLEDGE.md 时用 plugin 填充），插 filesystem 之后、default templates 之前。
- 注意：mtime 失效 + session 缓存兼容（invasive，故 Phase B 暂缓）。`PluginRegistry` 已记 `record.provides.behavior` 作扩展点。

## §3 channel-as-plugin 扩展点（Model B 已开代码面，但仍推迟）

- 任务"channel-as-plugin 留后续" + H1 binding 收敛复杂度。Model B 已能跑代码，但 channel adapter 经 PlatformRegistry 注册涉 H1（binding 须收敛主 Agent，F105 `upsert_configured_binding` 守卫）。
- 落地：plugin 提供 `ChannelAdapter` 类 + config schema + route 描述，loader 注册进 `PlatformRegistry`（复用其 fail-fast 降级）。**H1 MUST**：binding `agent_profile_id=""`（主 Agent）。
- ⚠️ **诚实**：Model B 下 code plugin 可直接调 PlatformRegistry（运行时无法强制 H1）→ 靠 trust+audit，真 enforcement = v0.2 沙箱。

## §4 Companion（M7）

F106 是 Companion 装载基础设施前置。Companion 推 M7。

## §5 关键设计资产（后续复用）

- **专用 plugin tool-load path**（`plugin_loader.py`）：namespaced `octoagent_plugins.<name>.*` + staging 冲突预检 + 事务回滚 + **MED-1 全局 register 篡改检测**（import 前后 registry 快照 diff + 还原）。Phase C/扩展点新增 code 入口复用。
- **审批模型**：`.approved` marker（code_hash）+ human-initiated `POST /approve` + 换码 reconcile 自动 pending。
- **降级二分**：注册器 fail-fast vs 单 plugin try/except 隔离（#6）。
- **§0.3 residual + 风险披露**：审批面禁"已扫描/安全"措辞，明示"运行任意代码"。

## §6 LOW 归档（合入后顺手 / F108）

- LOW-1 symlink 目录遍历：3.13+ 迁移时 `recurse_symlinks=False`（code_hash.py + skills/plugins/discovery.py）。
- LOW-2 remove() 容纳：统一 `_ensure_within` helper（三处：discovery/loader/registry.remove）。
- LOW-3 `_emit` `task_store._conn` 私有：随 store 接口公共化收口。
