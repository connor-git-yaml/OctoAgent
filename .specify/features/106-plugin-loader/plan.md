# F106 User Plugin Loader — Implementation Plan

**Spec**: [spec.md](./spec.md) v0.3（review round-1 闭环）
**Baseline**: f3d8a267
**分阶段**: spec §13 —— Phase A（declarative 安全切片）→ B（code+审批）→ C（watchdog+git）。**本 plan 详 Phase A，B/C roadmap。**
**集成点（已实测核实）**:
- `SkillDiscovery` 构造于 `apps/gateway/src/octoagent/gateway/services/capability_pack.py:159`（builtin/user/project dir）；暴露 `app.state.skill_discovery`（`deps.py:92 get_skill_discovery`）。
- OctoHarness 11 段 bootstrap（`octo_harness.py:167-177`）：段 7 `_bootstrap_capability_pack`(173) → 段 8 `_bootstrap_mcp`(174) → 段 9 `_bootstrap_executors`(175)。**plugin 段 7.5 插 173↔175 之间**。
- `EventType` 枚举：`packages/core/src/octoagent/core/models/enums.py`。
- 路由：`main.py:345 protected=[Depends(require_front_door_access)]`；`app.include_router(skills.router, dependencies=protected)`(364)。
- `ContentThreatScanService.scan_memory(content)->ThreatScanResult`（同步，查 `.blocked`），`content_threat_scan.py`。
- 中央 ToolRegistry `register()` 覆盖语义 + `scan_and_register` 执行代码（Phase B 用，**不** reuse）。

---

## 1. 架构总览

新包/模块（落 `packages/skills`，与 SkillDiscovery 同栈，复用其 manifest/discovery 范式）：

```
packages/skills/src/octoagent/skills/plugins/
  __init__.py
  manifest.py        # PluginManifest / PluginCapability / PluginState / PluginRejectedReason
  registry.py        # PluginRegistry：发现/分类/扫描/注册/toggle/refresh/降级（asyncio.Lock）
  discovery.py       # 纯 stat 发现 + 能力分类（绝不 import）
  code_hash.py       # 整树 hash（Phase B 用，Phase A 占位/不调）
  (approval.py       # Phase B：审批 + .approved marker)
  (loader.py         # Phase B：importlib 专用 tool-load path)
  (watcher.py        # Phase C：watchdog observer)
  (git_ops.py        # Phase C：git clone/pull 硬化)
```

REST：`apps/gateway/src/octoagent/gateway/routes/plugins.py`（仿 `routes/skills.py`）。
DI：`octo_harness` 新增 `plugins_dir` 参数 + 段 7.5 `_bootstrap_user_plugins`。
SkillDiscovery 扩展：`scan(plugin_skill_dirs=...)` + plugin 源 reject-on-collision（lowest priority）。
behavior overlay：`agent_decision.resolve_behavior_pack` source_chain 插 plugin fallback 源。
EventType：`enums.py` 加 `PLUGIN_LOADED/REJECTED/TOGGLED/REMOVED`（A）+ `PLUGIN_APPROVED/CODE_CHANGED`（B/C）。

---

## 2. Phase A 详细设计（declarative 安全切片，无代码执行/watchdog/git）

### 2.1 数据模型（`plugins/manifest.py`）

```python
class PluginCapability(StrEnum): DECLARATIVE="declarative"; CODE="code"
class PluginState(StrEnum): ENABLED="enabled"; DISABLED="disabled"; PENDING_APPROVAL="pending_approval"; REJECTED="rejected"
class PluginRejectedReason(StrEnum):
    MANIFEST_INVALID="manifest_invalid"; NAME_MISMATCH="name_mismatch"; NAME_INVALID="name_invalid"
    MISSING_ARTIFACT="missing_artifact"; NAME_COLLISION="name_collision"; THREAT_FLAGGED="threat_flagged"
    BEHAVIOR_NOT_ALLOWED="behavior_not_allowed"; IMPORT_ERROR="import_error"; APPROVAL_MISSING="approval_missing"
    PATH_ESCAPE="path_escape"; UNKNOWN="unknown"
class PluginManifest(BaseModel):  # 未知字段宽容（model_config extra="ignore"）
    name: str; version: str=""; description: str=""; author: str=""; repo: str=""
    provides: PluginProvides  # skills:list[str] / behavior:list[str] / tools:list[str]=[] / hooks:bool=False / extensions:list[str]=[]
class PluginRecord(BaseModel):
    name/version/description/state/capability/source/provides/code_hash?/reject_reason?/scanner_skipped?
```
name 校验复用 skill 的 kebab `^[a-z0-9]+(-[a-z0-9]+)*$`（`skill_models.py:28` 范式）。

### 2.2 发现 + 纯 stat 分类（`plugins/discovery.py`，FR-1.1/1.3/1.4）

- 扫 `plugins_dir/*`（一级），含 `plugin.yaml` 者为候选；无则跳过。
- 解析 manifest（`yaml.safe_load` — 注意非 `unsafe_load`）→ `PluginManifest`；校验 name kebab + 与目录名一致 + `provides.skills` 子目录含 SKILL.md + `provides.behavior` ∈ `{KNOWLEDGE.md}`。
- **纯 stat 分类**（review H7）：`os.walk`/`Path.glob` 找 `*.py/*.pyc/*.so/*.dylib/*.pyd/*.pyx/*.pth/conftest.py/setup.py/pyproject.toml` 之一 → `CODE`；否则 `DECLARATIVE`。**绝不 import / 不加 sys.path**。
- 任一校验失败 → `PluginRecord(state=REJECTED, reason=...)`（不抛，FR-4.2）。
- **Phase A 行为**：`CODE` plugin 发现+列出但**状态 `pending_approval`，不注册其制品**（审批/加载是 Phase B）；`DECLARATIVE` plugin 进注册流程。

### 2.3 注册（`plugins/registry.py`，FR-3.1/3.5/4.2/4.4）

`PluginRegistry`（`asyncio.Lock` 串行 discover/register/toggle/refresh）：
- **skill**：收集所有 enabled declarative plugin 的 `skills/*`（含 SKILL.md）目录 → 喂 SkillDiscovery（见 2.5）。
- **behavior**：plugin `provides.behavior`（仅 `KNOWLEDGE.md`）登记为 fallback overlay 源（见 2.6）。
- 威胁扫描（2.4）在注册前。
- 每个 declarative plugin 成功 → `PluginRecord(state=ENABLED)` + 写 `PLUGIN_LOADED`；失败 → `REJECTED` + `PLUGIN_REJECTED`。
- 降级：单 plugin 任一步异常 try/except 隔离 + 审计 + 继续（不拖垮）。

### 2.4 威胁扫描（FR-5，review H4）

- manifest 文本 + 每个 SKILL.md body + KNOWLEDGE.md → `ContentThreatScanService.scan_memory(content)`（同步）。
- `result.blocked` → 拒载（`THREAT_FLAGGED`）；scanner **抛异常** → fail-open + `scanner_skipped=true` + warning；返回 **degraded BLOCK**（超 2MB）→ 拒载。
- 审计无原文（payload 仅 pattern_id/severity/plugin name）。

### 2.5 SkillDiscovery 扩展（FR-3.1，review L11）

`discovery.py`：
- `scan()` 加可选 `plugin_skill_dirs: list[tuple[str, Path]]`（(plugin_name, skill_root)）；**plugin 源最先扫描（最低优先级）**——但语义**反转为 reject-on-collision**：plugin skill name 已存在（builtin/user/project 或先注册 plugin）→ **跳过 + 审计**（不覆盖，防劫持）。
- `SkillMdEntry` 加 `provenance: str | None`（plugin name；非 plugin None）。
- 实现：plugin 扫描单独走一个 `_scan_plugin_directory`，对 cache 已有 name 跳过（非覆盖）；或两遍（先记非 plugin name 集，plugin 名命中即拒）。**保持 scan 原子缓存替换**（`discovery.py:158`）。
- **兼容性**：`scan()` 无 plugin_skill_dirs 时**字节级等价 baseline**（0 regression 不变量）。

### 2.6 behavior overlay fallback（FR-3.5，review DP-11/CL-2）

`agent_decision.resolve_behavior_pack`（`agent_decision.py:180`）source_chain：在 filesystem 之后、default templates 之前插 plugin 源——**fallback-fill**：仅当现有源缺该 file id 时用 plugin `KNOWLEDGE.md`。allowlist 硬限 `{KNOWLEDGE.md}`（越界拒+审计）。路径守卫不逃逸 plugin 目录。mtime 失效兼容。**Phase A 可选**（若复杂度高，behavior overlay 可降为 Phase A.5 / B 前，skill 是 Phase A 主价值）。

### 2.7 REST（`routes/plugins.py`，FR-8）

仿 `routes/skills.py`：`GET /api/plugins`(200) / `GET /{name}`(200/404) / `POST /{name}/toggle`(200/404) / `DELETE /{name}`(204/403/404，仅 plugins_dir 内 `_ensure_path_within`) / `POST /refresh`(200 计数)。`main.py` `app.include_router(plugins.router, dependencies=protected)`。
（approve / install / update 是 B/C。）

### 2.8 toggle（FR-3.1/3.3）

`.disabled` marker：存在=禁用。toggle = 增/删 marker + refresh。declarative toggle 自由（无审批）。落盘跨重启。

### 2.9 bootstrap 段 7.5（FR-10，review CL-9）

`octo_harness.py`：
- 新增 `self._plugins_dir` DI 参数（None=生产 `~/.octoagent/plugins`；非 None=tmp 隔离，对齐 `_mcp_servers_dir` 范式 `octo_harness.py:478`）；不存在 mkdir。
- 新 `_bootstrap_user_plugins(app)`，在 `bootstrap()` 序列 `_bootstrap_capability_pack`(173) 之后、`_bootstrap_executors`(175) 之前调用。
- 构造 `PluginRegistry(plugins_dir, skill_discovery=app.state.skill_discovery, content_scanner=ContentThreatScanService(), event_store=...)` → discover + register declarative → `app.state.plugin_registry`。
- **整段 try/except**（`app.state.plugin_registry=None` 降级，FR-10.2）。
- shutdown：Phase A 无 observer，仅置 None（C 加 observer teardown）。

### 2.10 EventType（FR-9.1）

`enums.py` 加 `PLUGIN_LOADED / PLUGIN_REJECTED / PLUGIN_TOGGLED / PLUGIN_REMOVED`（Phase A）。payload：name/version/state/capability/reason/source（无原文）。

---

## 3. Phase B/C Roadmap（本会话不实施，handoff 交接）

**Phase B（code+审批，安全核心）**：
- `code_hash.py` 整树 hash（全文件排序 path+content sha256）。
- `approval.py`：`.approved` marker 记 code_hash（持久，对齐 `.disabled` 文件系统 SoT）；bootstrap 仅自动加载 hash 匹配的 enabled code plugin。
- `loader.py`：专用 importlib path（namespaced `octoagent_plugins.<name>.*`）+ staging 冲突预检 + 事务 `registry.register()` + 失败回滚 `sys.modules.pop`（**非** scan_and_register，DP-3）。
- `POST /approve`（human-initiated）+ `PLUGIN_APPROVED` + pending_approval 惰性不 import（FR-2.1 契约测试 sys.modules 不含）。
- hooks lifecycle（on_load/on_unload）。
- §0.3 honest 限制语言落 UI/审批面（FR-8.3 风险披露）。
- **Gate B：Codex + 第二模型双评审 0 HIGH 再 Phase C。**

**Phase C（watchdog+git）**：
- `watcher.py`：watchdog Observer + debounce + declarative auto-refresh + code 变更→pending_approval（deregister 工具 + sys.modules evict 在锁下 + 防 reload loop，DP-6）+ DI flag 关。
- `git_ops.py`：clone/pull 硬化（scheme allowlist 禁 ext::、`--` 终止、`-c protocol.ext.allow=never -c core.hooksPath=/dev/null` + env scrub + temp-then-move + symlink-.git 拒，DP-7）。
- `POST /install` + `POST /{name}/update` + `PLUGIN_CODE_CHANGED` + shutdown observer teardown。

---

## 4. 测试策略

- **Phase A 单测**（`packages/skills/tests/test_plugin_*.py` + `apps/gateway/tests/.../test_plugins_api.py`）：发现/分类/manifest 校验/skill 注册 PLUGIN source/名冲突拒/威胁拒/fail-open vs degraded-block/behavior allowlist/toggle 持久/REST 契约/降级隔离。
- **Phase A e2e**（`apps/gateway/tests/e2e/`）：`plugins_dir` DI tmp 放 [好 declarative, 坏 manifest, 威胁命中, 名冲突, code-capable] → 断言好的注册 + 坏的隔离拒 + code-capable pending_approval 不注册 + gateway 正常（SC-002/005/006/010）。
- **0 regression**：`scan(plugin_skill_dirs=None)` 字节级等价；全量 `pytest` + `pytest -m e2e_smoke`（PYTHONPATH 锁 worktree，禁 worktree uv sync）。
- **AC↔test 绑定**：spec §9。

## 5. 风险 / 精度项（plan→impl）

- SkillDiscovery scan 扩参的 0 regression：plugin_skill_dirs=None 默认严格等价（mock fixture 依赖）。
- behavior overlay 接入 `resolve_behavior_pack` 的 session 级缓存 + mtime 兼容——若 invasive，Phase A 可暂缓 behavior（skill 为主价值），plan §10 标。
- `_repo_root` 计算（capability_pack.py:156 `parents[7]`）——plugin 不依赖 repo_root，用 plugins_dir DI。
- EventType 新增需查 OctoBench scorer 集（F114 先例）+ 事件消费者兼容。
- recon 行号 review L10 标 stale，impl 以本 plan 实测行号为准。

## 6. 实施顺序（Phase A 内）

T1 数据模型（manifest.py）→ T2 discovery（纯 stat）→ T3 EventType → T4 SkillDiscovery 扩参 + provenance → T5 PluginRegistry（注册+扫描+降级）→ T6 behavior overlay（可选/暂缓）→ T7 bootstrap 段 7.5 + plugins_dir DI → T8 REST → T9 单测 → T10 e2e + 0 regression → T11 living-docs。
