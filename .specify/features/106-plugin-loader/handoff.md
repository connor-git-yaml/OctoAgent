# F106 Handoff —— 余项 + 扩展点

> Phase A + B + C 全交付（completion-report.md）。本文交接剩余 follow-up + 扩展点。

## §1 Phase C ✅ 已完成（本会话）

watchdog 热重载（`plugin_watcher.py`，lazy import + 降级）+ git 安装/更新（`plugin_git.py`，H8 硬化）+ `PLUGIN_CODE_CHANGED` + `POST /install`/`/update` 全交付。红队实证抓出并修复 **H-1 symlink RCE**（latent 自 Phase A+B）。

**Phase C nice-to-have（归档，v0.1 可接受，未来顺手）**：
- N1：`registry.update()` 持锁跨 `git pull`（网络）阻塞其他 registry op——单用户可接受；优化 = pull 进 temp worktree 外锁、swap 内锁。
- N2：`git_install` temp dir 用 `tempfile.mkdtemp(prefix=".tmp-")`（dot-prefix）显式靠 iter 的 dot-filter，而非"manifest 在 temp 深一层"的隐式不被 reconcile 拾取。
- N5：watcher `_refresh_inflight` 跳过的 coalesced 事件不 re-arm——末尾事件可能漏到下次 fs 事件；优化 = skip 时置 dirty flag + finally re-arm。
- 测试 gap：git_install 真实 local-remote clone happy-path（现仅 monkeypatch 编排 + update 真实 pull 覆盖共享 `_run_git`/`_check_tree_safe`）；树内 symlink-escape（现仅 symlink-.git + validate_no_symlinks 全拒覆盖）；update→re-approval 整链（现 halves 各测）。

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
