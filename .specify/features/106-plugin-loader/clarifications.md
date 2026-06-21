# F106 Plugin Loader — Clarifications

> 生成日期：2026-06-21
> 范围：spec v0.1 draft（f3d8a267 baseline）；不重复 DP-1/DP-6/DP-7（已走 GATE_DESIGN）。

---

## CL-1：SkillDiscovery 集成机制 — 两种方案需定稿

**歧义**：spec §10 item 1 说"倾向前者（扩 `SkillDiscovery.scan` 接受 plugin skill dirs）"但未定稿。两方案行为差异明显：

- **方案 A**：`SkillDiscovery.scan` 接收额外 plugin skill dirs list，内部统一扫描、原子替换缓存（PLUGIN source 与 BUILTIN/USER/PROJECT 同一替换周期）
- **方案 B**：`PluginRegistry` 独立扫描后 inject entries 到 SkillDiscovery（需 SkillDiscovery 暴露 `inject_external` API，两个替换时间点）

**为什么重要**：影响名冲突检测时机（scan 时 vs inject 时）、refresh 原子性（一次还是两次原子替换）、`SkillDiscovery.scan` 是否成为必须修改的稳定接口。

**推荐**：方案 A。理由：保持原子缓存替换语义，名冲突在同一 scan 周期内按 tier 顺序检测，refresh 仅一次替换，不需要 SkillDiscovery 暴露新注入 API。具体实现：`scan()` 接受可选 `plugin_dirs: list[Path] = []` 参数，在扫描 BUILTIN/USER/PROJECT 后扫 PLUGIN tier，构建时记 provenance。

[AUTO-RESOLVED: 方案 A — 扩 `scan()` 签名接受 plugin_dirs]

---

## CL-2：behavior overlay 接入点 — `resolve_behavior_pack` 的精确 merge 语义

**歧义**：spec 说 plugin behavior 是"最低优先级覆盖源"，但 `resolve_behavior_pack` 现有三路解析（filesystem → metadata raw → default templates），这个"最低优先级"在哪一路之后、如何 merge 并不明确。具体缺口：

1. plugin 提供 `KNOWLEDGE.md` 时，merge 是"附加到现有 KNOWLEDGE.md 末尾"还是"仅当 KNOWLEDGE.md 缺失时填充"？
2. 若 project/user/system 的 KNOWLEDGE.md 全部缺失，plugin 的是否生效？
3. plugin 提供多个 behavior 文件时，order 如何？多个 plugin 各提供 KNOWLEDGE.md 时，merge 顺序？

**为什么重要**：这直接决定用户能否预期 plugin 知识"是否被看见"，测试断言写法也不同（覆盖 vs 追加 vs 仅填充）。

**推荐**：
- merge 语义：**"仅当该 behavior 文件 id 在现有源（filesystem + metadata）中缺失时，plugin 的内容作为 fallback 填充"**。不做字符串拼接（避免 LLM 上下文冗余），符合"最低优先级"直觉。
- 若多个 plugin 均提供同一 file id：先注册的 plugin 胜，后续被拒（同 DP-2 先注册胜原则），写 `PLUGIN_REJECTED(reason=behavior_name_collision)`。
- 接入点：在 `resolve_behavior_pack` 的 filesystem 路径之后、default templates 之前插入 plugin overlay check（第 1.5 路）。

[AUTO-RESOLVED: fallback-fill 语义 + 先注册胜 + 插在 filesystem 之后 default 之前]

---

## CL-3：`PLUGIN_REJECTED` 的 reason 枚举 — 缺精确列表

**歧义**：spec 散落多处提到不同 reason 字符串（`manifest_invalid` / `missing_artifact` / `threat_flagged` / `name_collision` / `behavior_not_allowed` / `name_mismatch`），但没有一处集中的枚举定义。`reason=` 是自由字符串还是枚举？payload 其他字段（plugin_name / version / pattern_id / collision_with）包含哪些？

**为什么重要**：audit event schema 需在 plan 阶段定稳，否则测试断言会写出不一致的字符串，后续 OctoBench scorer 也要列举新 EventType。

**推荐**：定义 `PluginRejectedReason` StrEnum，完整值：

```python
class PluginRejectedReason(StrEnum):
    MANIFEST_PARSE_ERROR = "manifest_parse_error"     # YAML 解析失败
    MANIFEST_INVALID = "manifest_invalid"             # schema 校验失败（必填缺/格式错）
    NAME_MISMATCH = "name_mismatch"                   # manifest.name != 目录名
    NAME_INVALID = "name_invalid"                     # 非 kebab / 含路径穿越
    MISSING_ARTIFACT = "missing_artifact"             # provides 引用的子目录/文件不存在
    THREAT_FLAGGED = "threat_flagged"                 # ThreatScanner BLOCK 命中
    SKILL_NAME_COLLISION = "skill_name_collision"     # skill 名与 builtin/user/project 冲突
    BEHAVIOR_NOT_ALLOWED = "behavior_not_allowed"     # behavior file id 不在 allowlist
    BEHAVIOR_NAME_COLLISION = "behavior_name_collision" # 同 file id 另一 plugin 已注册
    SCANNER_ERROR = "scanner_error"                   # ThreatScanner 内部异常（fail-open 模式下仍装载）
```

`PLUGIN_REJECTED` payload：`{name, version?, reason: PluginRejectedReason, detail?: str, collision_with?: str}`（`detail` 不含原文，仅 pattern_id 或模型字段名）

[AUTO-RESOLVED: 定义 PluginRejectedReason StrEnum，payload 含 collision_with]

---

## CL-4：ThreatScanner fail 模式 — spec 措辞未锁定

**歧义**：spec §4 edge cases 写"倾向 fail-open 装载 + 审计 warning"，§10 item 4 写"plan 定稿"，FR-4.2 只管命中 BLOCK 的情况，**扫描器本身异常**（网络、内部 bug、内存）时的行为在 FR 中缺失。

**为什么重要**：fail-open（扫描器崩溃=允许装载）与 fail-closed（崩溃=拒载）安全语义截然不同，需在 FR 层明文锁定，测试须覆盖这条路径。

**推荐**：**fail-open**（scan 异常 → `PLUGIN_REJECTED(reason=scanner_error)` 不写，改写 `PLUGIN_LOADED` + warning log + `scanner_skipped=True` 字段）。理由：①装载期是离线一次性、非网络调用，异常率极低；②过于保守的 fail-closed 会在 ContentThreatScanService 本身有 bug 时让所有 plugin 无法使用；③F124 对 tool 结果也是 flag-not-block 语义。新增 FR-4.5：`ContentThreatScanService` 异常 MUST 降级（log warning + 继续装载 + `PLUGIN_LOADED(scanner_skipped=True)`），MUST NOT 拒载。

[AUTO-RESOLVED: fail-open，scanner_skipped 字段标记，FR-4.5 补入 plan]

---

## CL-5：REST 响应契约 — 状态码 + 响应 body shape 缺规格

**歧义**：FR-6 列出了端点语义，但以下细节缺失：

1. `POST /api/plugins/{name}/toggle` 返回什么？当前状态（enabled/disabled）？操作前后状态？
2. `DELETE /api/plugins/{name}` 成功返回 200 还是 204？
3. `POST /api/plugins/refresh` 返回什么？刷新后 loaded/rejected 数量摘要？
4. `GET /api/plugins` 的 `provides` 摘要深度——是 `{skills: ["name-a"], behavior: ["KNOWLEDGE.md"]}` 还是含 skill body？
5. 不存在的 plugin toggle/delete → 404 还是 422？

**为什么重要**：REST 契约测试（SC-009）需要锁定 shape，否则测试写不稳。

**推荐**：

| 端点 | 成功状态码 | body shape |
|------|-----------|-----------|
| `GET /api/plugins` | 200 | `{plugins: PluginListItem[]}` |
| `GET /api/plugins/{name}` | 200/404 | `PluginRecord` |
| `POST /api/plugins/{name}/toggle` | 200 | `{name, enabled: bool}` (操作后状态) |
| `DELETE /api/plugins/{name}` | 204 | — |
| `POST /api/plugins/refresh` | 200 | `{loaded: int, rejected: int, disabled: int}` |

- `GET /api/plugins` 的 `provides` 仅含 name list，无 body。
- toggle/delete 不存在的 plugin → 404。
- 路径越界 delete → 403。

[AUTO-RESOLVED: 上表锁定 REST 契约]

---

## CL-6：`provenance` 字段 — SkillMdEntry 是否新增字段

**歧义**：spec FR-2.1 说 skill 注册要记 provenance（plugin name），但 `SkillMdEntry`（`skill_models.py:31`）现有字段是 `name/version/description/author/tags/trigger_patterns/tools_required/metadata/resource_limits`，没有 `source` 和 `provenance`。`SkillSource` 是调用方知道（通过 tier 顺序），但存在 entry 本身里吗？

**为什么重要**：`GET /api/skills` 和 `skills list` 工具需要能区分哪个 skill 来自哪个 plugin；测试断言 `source=PLUGIN` 需要一个可查字段。

**推荐**：在 `SkillMdEntry` 新增两个可选字段：`source: SkillSource = SkillSource.BUILTIN` 和 `provenance: str | None = None`（PLUGIN source 时为 plugin name）。`SkillDiscovery.scan` 在构造 entry 时填充这两字段。`GET /api/skills` response 透传这两字段。

[AUTO-RESOLVED: SkillMdEntry 新增 source + provenance 字段]

---

## CL-7：behavior allowlist — TOOLS.md 的安全性未讨论

**歧义**：spec DP-11 说"v0.1 建议限 `KNOWLEDGE.md` / `TOOLS.md`"，但 TOOLS.md 内容是"工具使用说明和策略"，plugin 写入 TOOLS.md 实质上可改变 LLM 对工具使用方式的理解，接近 Policy 层影响。spec §2.2 明确说"plugin 改 Policy / 新增工具 schema → 不做（#9/#10）"，TOOLS.md overlay 与这条规则的边界未说清。

**为什么重要**：如果 TOOLS.md 被允许进入 allowlist，plugin 可通过 TOOLS.md 提示 LLM "当做 X 时调用工具 Y 并传参 Z"，隐性放大工具使用面——与 #9/#10 冲突风险。

**推荐**：**v0.1 allowlist 仅含 `KNOWLEDGE.md`，TOOLS.md 移除**。理由：`KNOWLEDGE.md` = 领域知识文本，对工具访问策略无影响；`TOOLS.md` = 工具使用说明，plugin 写此文件可引导 LLM 以非预期方式调用工具，过于接近 #9/#10 红线。TOOLS.md 可在 v0.2 加信任模型升级后开放。

[NEEDS-HUMAN] 这是安全决策，影响 plugin 能提供的价值上限。推荐去掉 TOOLS.md，但若用户认为 TOOLS.md 仅作"工具使用 tips"而非 Policy 干预，可以纳入。请确认：**v0.1 behavior allowlist = `[KNOWLEDGE.md]` only，还是 `[KNOWLEDGE.md, TOOLS.md]`？**

---

## CL-8：refresh 并发 — PluginRegistry 与 SkillDiscovery 的原子协调未定

**歧义**：spec §4 edge cases 提到"refresh 并发 → SkillDiscovery.scan 已原子替换缓存，plugin_registry refresh 须同样原子"，但没有说明如何协调：若并发两个 `POST /refresh` 同时触发，或 toggle + refresh 并发，plugin_registry 的状态（loaded set）与 SkillDiscovery 缓存之间是否可能短暂不一致？

**为什么重要**：plan 阶段须决定是否需锁，以及锁的粒度（asyncio.Lock 在 PluginRegistry 上），否则 test_toggle_disable_enable_persists 可能出现 race。

**推荐**：`PluginRegistry` 用 `asyncio.Lock` 保护 refresh 路径（整个 scan → 注册流程），toggle 触发 refresh 也在锁内。锁粒度：PluginRegistry 级别（不影响 SkillDiscovery 自身锁），refresh 完成后 `SkillDiscovery.scan(plugin_dirs=...)` 一次性替换缓存。并发 refresh 请求后来者等锁、不重入（等前者完成后直接用新结果，不 double-scan）。

[AUTO-RESOLVED: asyncio.Lock 在 PluginRegistry.refresh，不重入]

---

## CL-9：bootstrap 段顺序 — capability_pack 构造与 plugin_registry 的依赖关系

**歧义**：spec FR-8.1 说"段 7/8 之后、段 9 之前"，但段 7 是 `_bootstrap_capability_pack`（SkillDiscovery 在此构造），plugin_registry 需要 SkillDiscovery 实例才能注册 skill。问题：是在 `_bootstrap_capability_pack` **之内**扩展（plugin skill 作为 scan 参数传入），还是新建独立 `_bootstrap_user_plugins` 段在段 7 **之后**、并拿段 7 产物 SkillDiscovery？

**为什么重要**：影响 DI 线路（PluginRegistry 构造时机、谁持有 SkillDiscovery 引用）和 OctoHarness 改动范围。

**推荐**：新建 `_bootstrap_user_plugins` 段在段 7 之后，接收 `app.state.skill_discovery`（段 7 产物）并调用 `skill_discovery.scan(plugin_dirs=enabled_plugin_skill_dirs)`。不修改 `_bootstrap_capability_pack` 内部。这样 PluginRegistry 在段 7.5 产出，存入 `app.state.plugin_registry`；行为 overlay 接入点在 `resolve_behavior_pack` 调用时从 `app.state.plugin_registry` 拿 plugin behavior。

[AUTO-RESOLVED: 独立 _bootstrap_user_plugins 段 7.5，不改 _bootstrap_capability_pack]

---

## CL-10：DELETE 端点 — 目录删除是否级联删 .disabled marker

**歧义**：`DELETE /api/plugins/{name}` 删除 plugin 目录，FR-6.4 说"删 `~/.octoagent/plugins/` 内目录"。但 `.disabled` marker 在目录内，目录整个删掉自然带走它。问题：若 plugin 已禁用（有 `.disabled`）时被 DELETE，是否有任何特殊处理还是直接 shutil.rmtree？

**为什么重要**：确认实现是否简单（shutil.rmtree + registry cleanup），不需要特殊路径，plan 可简化。

**推荐**：**无特殊处理**，shutil.rmtree 删整个 plugin 目录（`.disabled` 自然消失）+ 从 PluginRegistry loaded 集合移除 + `PLUGIN_REMOVED` 审计（建议新增此 EventType，FR-7 缺此场景）。

[AUTO-RESOLVED: shutil.rmtree 简单实现 + 建议新增 PLUGIN_REMOVED EventType]

---

## 摘要表

| # | 问题 | 自动解决 | 需人裁 |
|---|------|---------|--------|
| CL-1 | SkillDiscovery 集成机制（两方案） | 方案 A：扩 scan() 签名 | — |
| CL-2 | behavior overlay merge 语义 | fallback-fill，插 filesystem 之后 | — |
| CL-3 | PLUGIN_REJECTED reason 枚举缺失 | 定 PluginRejectedReason StrEnum | — |
| CL-4 | ThreatScanner fail 模式未锁 | fail-open + scanner_skipped 字段 | — |
| CL-5 | REST 状态码 + body shape 缺规格 | 锁定上表契约 | — |
| CL-6 | SkillMdEntry provenance 字段 | 新增 source + provenance 字段 | — |
| CL-7 | TOOLS.md 在 behavior allowlist 的安全性 | 推荐去掉 TOOLS.md | **[NEEDS-HUMAN]** |
| CL-8 | refresh 并发原子协调 | asyncio.Lock，不重入 | — |
| CL-9 | bootstrap 段顺序与 DI 依赖关系 | 独立段 7.5，不改段 7 内部 | — |
| CL-10 | DELETE 是否级联删 .disabled + 缺 PLUGIN_REMOVED | shutil.rmtree 简单实现，补 PLUGIN_REMOVED | — |
