# F106 User Plugin Loader — Completion Report（Phase A + B + C）

**分支**: feature/106-plugin-loader | **Baseline**: f3d8a267 | **状态**: Phase A + B + C 完成，未 push（待用户拍板）
**spec**: [spec.md](./spec.md) v0.3 | **plan**: [plan.md](./plan.md) | **spec review**: [spec-review-r1.md](./spec-review-r1.md)

---

## 1. 交付范围（实际做了 vs 计划）

GATE_DESIGN 用户拍板 **Model B（代码可执行）+ watchdog + git**；GATE_TASKS 用户拍板**本会话做 Phase A + B**。

| 阶段 | 计划 | 实际 |
|------|------|------|
| **Phase A**（declarative spine）| 发现/分类/注册/REST/降级 | ✅ 全做 |
| **Phase B**（code + 审批 + code-hash + 专用 loader + pending + hooks）| 安全核心 | ✅ 核心全做（含 hooks） |
| **Phase B 余项 behavior overlay**（FR-3.5）| spec §13 标"可选/暂缓" | ⏸ **显式推迟**（见 §4）|
| **Phase C**（watchdog + git）| 后续会话 | ✅ **本会话同步做**（用户"继续 Phase C"）|

### Phase A 产出（declarative 安全切片）
- `plugins_dir` DI（OctoHarness 参数）+ `OCTOAGENT_PLUGINS_DIR` env fallback + 独立 bootstrap 段 7.5 `_bootstrap_user_plugins`（capability_pack 后、executors 前，整段 try/except 降级）。
- `PluginManifest`/`PluginProvides`/`PluginCapability`/`PluginState`/`PluginRejectedReason`/`PluginRecord`（`packages/skills/.../plugins/manifest.py`）。
- 纯 stat 发现 + 能力分类（`discovery.py`，**绝不 import**；code 触发 = `.py/.pyc/.so/.dylib/.pyd/.pyx/.pth/conftest.py/setup.py/pyproject.toml/__pycache__`）。
- 威胁扫描经 `ContentThreatScanService.scan_memory`（manifest+SKILL.md+KNOWLEDGE.md；blocked→拒；scanner-raise→fail-open；oversize-degraded→拒）。
- SkillDiscovery 扩 `scan(plugin_skill_dirs=)` + `SkillSource.PLUGIN` + `SkillMdEntry.provenance`，**plugin 源 reject-on-collision 不覆盖**（防劫持内置 skill 名）。
- `/api/plugins` REST：list/get/toggle/approve/delete/refresh（front-door protected，状态码契约）。
- 4 EventType（`PLUGIN_LOADED/REJECTED/TOGGLED/REMOVED`）+ 单 plugin try/except 隔离降级（#6）。

### Phase B 产出（代码执行安全核心）
- **审批门控**：code-capable plugin 未审批 = `pending_approval`，**代码零 import**（FR-2.1，sys.modules 契约测试守护）。
- **整树 code_hash**（`code_hash.py`，全文件排序 path+content，非仅 .py；排除 .git/__pycache__/marker）。
- **审批持久化**（`.approved` marker 记 code_hash，跨重启；bootstrap 仅自动加载 hash 匹配的已审批 plugin）。
- **专用 loader**（`plugin_loader.py`，**非** scan_and_register）：namespaced 模块 + staging 冲突预检 + 事务注册 + 回滚 + sys.modules 清理。
- **换码重审**：reconcile 先 `_unload_all_code`（deregister 工具 + evict 模块，lock 下）再重建；code 变 → hash 不匹配 → pending（工具不残留、新码不自动执行）。
- **审批 = human-initiated** `POST /approve`（无 LLM 同轮可填 flag）+ 风险披露（禁"已扫描/安全"措辞）。
- **2 EventType**（`PLUGIN_APPROVED`，`PLUGIN_CODE_CHANGED` 定义待 Phase C producer）。
- **hooks lifecycle**（FR-3.4/FR-10.3）：已审批 code plugin 的 `hooks.py` on_load/on_unload，隔离调用。

### Phase C 产出（watchdog 热重载 + git 安装/更新）
- **watchdog**（`plugin_watcher.py`，FR-6）：lazy import + 优雅降级（watchdog 未装/observer 失败 → 禁用，手动 refresh 仍可）；observer 线程经 `run_coroutine_threadsafe` 桥到 asyncio loop（refresh 走 registry `asyncio.Lock`）；debounce + ignore 过滤（marker/.git/__pycache__）+ `_refresh_inflight`/`_stopped` 防叠加/post-stop race；declarative 变更自动 refresh / **code 变更 reconcile 自动转 pending_approval（复用 Phase B 换码闭合，不在 watcher 重造）**。
- **git**（`plugin_git.py`，FR-7，**H8 硬化**）：`validate_repo_url` scheme allowlist（https/ssh，禁 `ext::`/`fd::`/`file://`/`-`）+ `--` 终止符 + `-c protocol.ext.allow=never -c core.hooksPath=/dev/null -c core.fsmonitor=false` + scrub env（`GIT_TERMINAL_PROMPT=0`）+ temp-then-move + `_check_tree_safe`（symlink-.git/逃逸拒）+ kebab name + 不 clone-over-existing；provenance commit 从实际 `.git` 读。
- **registry/REST**：`install`（clone 不持锁）/`update`（pull 持锁）+ git source 检测 + `PLUGIN_CODE_CHANGED` emit（approved-code→pending 转换）；`POST /install` + `POST /{name}/update`。
- **bootstrap**：段 7.5 启 watcher（降级不拖垮）；shutdown 停 watcher + `registry.shutdown()`（on_unload）。pyproject 加 `watchdog>=4,<7`。

---

## 2. 测试 & 验证

- **F106 新测试 82 passed**：纯层 24 + 编排器 22（安全核心：pending-no-import / 换码重审 / 隔离降级 / 名冲突 / MED-1 全局 register 拦截 / hooks / N1 / N2 / **H-1 symlink 拒载** / install / code_changed）+ bootstrap 3 + git 12（repo_url 硬化 + 真实 local-remote pull + tree-safe）+ watcher 5（ignore/降级/debounce→refresh）+ REST 10 + full-lifespan e2e 1。
- **SkillDiscovery + code_hash 0 regression**：`packages/skills/tests/` **369 passed**；`scan(plugin_skill_dirs=None)` 字节级等价（baseline 守护）。
- **e2e_smoke 8/8 passed**（hermetic 隔离，conftest 加 `OCTOAGENT_PLUGINS_DIR` 重定向；watcher 在真 bootstrap 降级实证）。
- **全量回归**：Phase A+B `4098 passed`；Phase C `4130 passed / 0 failed`（`pytest packages apps -x`，EXIT=0，5m）。**0 regression vs f3d8a267**。

---

## 3. 双评审 panel（Codex + 第二模型，0 HIGH）

> 命中"新加载子系统 + 代码执行安全敏感"。**spec 阶段** 1 轮双 panel（9 HIGH 全闭环，见 spec-review-r1.md）；**实施阶段** 1 轮双 panel（安全红队 + Constitution/arch）。
> Codex CLI 因 OAuth 不稳定（memory project_openai_codex_oauth_renewal）本会话未跑，由两个独立 code-grounded 对抗 agent panel 替代（与 F103c "Codex 中断→主 session 接管"先例一致）。建议用户合入前可另跑 `/codex:adversarial-review` 复核。

**实施评审结论**：
- **安全红队：0 HIGH**——信任模型 gate **被代码强制**（未审批不 import 实测 airtight；整树 hash；reconcile unload-then-rebuild 闭合换码洞）。1 MED + 3 LOW。
- **Correctness：0-regression 成立**（代码层证明）。2 must-fix + 2 nice-to-have。

**闭环处理**：

| ID | 发现 | 处理 |
|----|------|------|
| MED-1 | plugin import 期直接调全局 `register()` 篡改既有工具（绕 staging 预检 + 非回滚）| ✅ **已修**：loader import 前后快照 registry，检测未授权篡改 → 还原 + 拒载（test_med1_global_register_blocked）|
| M1 | hooks（FR-3.4/FR-10.3）声明未实现 | ✅ **已实现**：on_load/on_unload 隔离 lifecycle（test_hooks_on_load_on_unload）|
| M2 | 缺 completion-report + §9 引用未建测试 | ✅ 本报告 + §9 deferred 标注（§4）|
| N1 | 审批后加载失败 → 每轮 re-exec 已知失败代码 | ✅ **已修**：失败路径 clear_approval → 回 pending（test_n1_failed_approval_clears_marker）|
| N2 | disable→enable 审批语义未测 | ✅ **已测 + 决议**：审批绑 code_hash，disable 不撤销；代码未变 re-enable 自动加载（FR-8.4 窄读，test_n2）|
| LOW-1 | symlink 目录遍历（3.13+）| ⏸ 归档：pinned 3.12 `rglob` 不跟随 symlink 目录，不活跃；3.13+ 迁移时加 `recurse_symlinks=False`（handoff）|
| LOW-2 | remove() 容纳检查风格不一致 | ⏸ 归档：route 路径参数单段 + kebab + is_dir 守卫，无可达逃逸；统一 helper 留 F108 顺手 |
| LOW-3 | `_emit` 触 `task_store._conn` 私有属性 | ⏸ 归档：沿用 daily_routine 既有范式，try/except 降级 |

**Phase A+B 实施 review：0 HIGH 残留。**

**Phase C 实施 review（双 panel：安全红队 + correctness，code-grounded）**：

| ID | 发现 | 处理 |
|----|------|------|
| **H-1** | **symlink 换码绕 code_hash 重审**（compute_tree_hash 跳过 symlink + loader resolve 跟随 + `_check_tree_safe` 仅 git 路径跑）→ 审批后换 symlink 目标 = 静默 RCE。**实施期红队实证（latent 自 Phase A+B）** | ✅ **已修**：`validate_no_symlinks` 拒任何含 symlink 的 plugin（reconcile 早跑）+ iter 跳 symlink 目录 + code_hash fold symlink（defense）+ loader 拒 symlink 模块路径。test_symlink_in_plugin_rejected / test_symlink_plugin_dir_skipped 守护 |
| L-1 | watcher stop 后 observer 残余事件触发 refresh | ✅ **已修**：`_stopped` guard（_on_event/_schedule_refresh/_trigger_refresh 早返回）|
| 安全其余 | git 传输/参数注入/hooks-on-pull/env scrub/路径容纳 + **H9 watcher TOCTOU 实测不存在**（reconcile 同步 unload-then-rebuild 无 loop yield，sync dispatch 不插入）| ✅ 实测全 closed |
| N1（correctness）| update 持锁跨 git pull（网络）| ⏸ 接受：单用户 + in-place pull 需与 reconcile 一致（install clone 已不持锁）|
| N2/N5 | install temp dir 显式 dot-prefix / watcher 丢弃 coalesced 事件 re-arm | ⏸ 归档 handoff（v0.1 可接受，手动 refresh 兜底）|
| 测试 gap | git_install 真实 clone happy-path / 树内 symlink-escape / update→re-approval 整链 | ⏸ 归档 handoff（halves 已各自覆盖，update 真实 pull + reconcile 换码各测）|

**Phase C 实施 review：1 HIGH（H-1，已修）+ 1 LOW（已修）+ 4 nice-to-have（归档/接受），0 HIGH 残留。**

---

## 4. 已知 limitations / 显式推迟（living-docs 漂移闸）

- **behavior overlay（FR-3.5/US4）推迟**：plugin `provides.behavior`（KNOWLEDGE.md）已**校验 + 威胁扫描**，但**未接入** `resolve_behavior_pack` overlay（grep 确认无半接线，干净推迟）。spec §13 标"可选/暂缓"；invasive（动 agent_decision session 缓存 + mtime）。→ Phase A.5 follow-up。
- **channel-as-plugin 扩展点推迟**（任务"留后续" + H1 复杂度，handoff §3）。
- **Phase C nice-to-have 归档 handoff**：N2（install temp dir 显式 dot-prefix）/ N5（watcher 丢弃 coalesced 事件 re-arm）/ 测试 gap（git_install 真实 clone happy-path、树内 symlink-escape 测）。N1（update 持锁跨网络）已接受。
- **§0.3 residual（v0.1 无沙箱）**：已审批 = 进程内任意代码，full 访问（secrets/网络/monkeypatch Policy）。MED-1 闭合"import 自动篡改"；on_load 等显式回调内 monkeypatch 属已审批代码残余 → v0.2 沙箱。
- **Blueprint 同步**：新 `docs/codebase-architecture/plugin-loader.md` 已产出；`docs/blueprint/` module-design/milestones 待主 session 收尾时同步（本会话产 living-doc，Blueprint 索引同步建议合入后做）。

---

## 5. 文件清单（净增）

**新增 src**（~1400 行）：
- `packages/skills/src/octoagent/skills/plugins/{__init__,manifest,discovery,code_hash,approval}.py`（纯层，无 gateway 依赖）
- `apps/gateway/src/octoagent/gateway/services/{plugin_registry,plugin_loader,plugin_git,plugin_watcher}.py`（编排器 / 专用 loader / git 硬化 / watchdog）
- `apps/gateway/src/octoagent/gateway/routes/plugins.py`（REST）

**修改**：
- `octo_harness.py`（plugins_dir DI + 段 7.5 + watcher start/stop）/ `main.py`（router）/ `enums.py`（6 EventType）
- `packages/skills/.../discovery.py`（scan plugin_skill_dirs + reject-collision + validate_no_symlinks）/ `skill_models.py`（PLUGIN source + provenance）
- `apps/gateway/pyproject.toml`（watchdog 依赖）/ `apps/gateway/tests/e2e_live/conftest.py`（plugins_dir hermetic 隔离）

**新增 test**（82 用例）：`packages/skills/tests/test_plugins.py` + `apps/gateway/tests/{services/test_plugin_registry,services/test_plugin_bootstrap,services/test_plugin_git,services/test_plugin_watcher,test_plugins_api,test_plugins_e2e}.py`

**制品**：`.specify/features/106-plugin-loader/{spec,plan,tasks,trace,spec-review-r1,completion-report,handoff}.md` + `research/recon.md`

---

## 6. 建议

**建议先 review 再合入 origin/master**（代码执行安全敏感）。Phase A+B+C 全 0 HIGH（Phase C 红队实证抓出并修复 H-1 symlink RCE）+ 0 regression（4130 passed），可合入。仅 behavior overlay（FR-3.5）+ channel-as-plugin 扩展点 + nice-to-have 推迟（handoff）。**合入前强烈建议另跑 Codex `/codex:adversarial-review` 复核**（本会话 Codex OAuth 不可用，由对抗 agent panel 替代——尤其 H-1 这类 latent 缝值得第三方 model 再扫）。
