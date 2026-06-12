# F108 Capability Layer Refactor — 分批规划（Phase 2，GATE 已拍板 2026-06-12）

> 基线：origin/master `d6148903`。状态：**用户已拍板，进实现**。
> **用户拍板记录（2026-06-12 AskUserQuestion）**：① 拆 F108a（W1-W5 域内机械拆分）/ F108b（W6-W8 跨层契约收口）两个 Feature；② AmbientRuntime 时间戳折入 F108b 独立 commit（显式行为变更标注 + 排除出零变更验证矩阵 + completion-report 单列）；③ 三项 feature 性质设计输入（schema 校验 / tool_call_id eviction / artifact read-back）全部 spin out 独立 Feature。
> 上游：`impact-report.md` + `recon/` 4 份。
> 双评审闭环：v2 已吸收 Codex（F1-F8，`review-codex-plan.md`）+ Opus（O1-O8，`review-opus-plan.md`）全部接受项；分歧人裁项见 §3.5。
> 排序原则：ROI ÷ 风险，依赖前置，同文件 wave 严格串行；**每 wave 可独立合入 master 并独立验证**。

## 0. 方法论（每 wave 强制，F113 已验证范式）

1. **字节级对账**：搬运块抽取前后逐字节 diff（允许的唯一差异：import 行、class 头/缩进、mixin 声明）；产出对账清单（F113 范式：90/91 唯一豁免需逐条记录理由）。
2. **禁 `ruff I001 --fix` 搬运文件**（会重排函数内 lazy import 破坏行为）；lazy import 原位保留。
3. **helpers 抽叶子破环**：无 self 引用的纯函数 → `*_helpers.py`；测试直调私有方法的 → **mixin 继承**（方法保持在类上）；跨簇编排根 → **留主类**。
4. **回归门**：每 wave 后 PYTHONPATH 锁定全量回归（`uv run --no-sync python -m pytest`，禁 uv sync）vs baseline 0 regression + e2e_smoke 8/8（pre-commit hook）。
5. **残留扫描**：旧符号/旧 import 路径全仓 grep 零残留（豁免：docs 历史引用）。
6. **双评审 panel**：每 wave commit 前 Codex adversarial + Opus 第二模型 review，分歧人裁，0 HIGH 残留。
7. **不主动 push**；每 wave 完成回报，最终用户拍板合入。

## 1. Wave 总览（8 waves）

| Wave | 内容 | 主文件 | 规模 | 风险 | ROI | 依赖 |
|------|------|--------|------|------|-----|------|
| **W1** | behavior 域收口：behavior_workspace 拆包 + D12 写序列收口 + worker_service C2 域泄漏回迁 | behavior_workspace.py / worker_service.py / misc_tools.py | ~1900 行重组 | 低 | **最高** | 无 |
| **W2** | coordinator 瘦身 + D11 改名 | _coordinator.py / orchestrator.py | ~700 行抽出 | **最低** | 高 | 无 |
| **W3** | setup_service 拆分（mixin + helpers） | setup_service.py | ~1200 行抽出 | 中 | 高 | 无 |
| **W4** | worker_service + session_service 拆分 | worker_service.py / session_service.py | ~900 行抽出 | 中 | 中 | W1（同文件） |
| **W5** | capability_pack 拆分（5 mixin） | capability_pack.py | ~900 行抽出 | **中高** | 高 | 无 |
| **W6** | D9 跨层归位 + living-docs：`_ApprovalOverrideMemoryCache` 下沉 tooling + 三层职责文档化 | capability_pack.py / tooling / docs | ~60 行移动 + docs | 低 | 中 | W5（同文件） |
| **W7** | F118 D8 typed DI：typed service registry + typed accessor + bind_* typed 化 | _base.py / _coordinator.py / setup_service.py / mcp_service.py | ~150 行改写 | 中 | 中 | W2/W3（同文件） |
| **W8** | 顺手项：F125 LOWs + **AmbientRuntime 时间戳挪出冻结前缀（显式行为变更）** | threat_scanner 相关 / agent_context_prompt_assembly.py | 小 | 低（但含 1 项行为变更） | 中 | 无 |

并行可能性：W2/W3/W5 互不触碰同文件，理论可并行 worktree；但**双评审 + 合入节奏建议串行**（每 wave rebase 上一 wave 的 master，冲突面为零）。

## 2. 每 wave 明细

### W1：behavior 域收口（预计 3 commits）
- **C1** behavior_workspace.py（1741）→ `behavior_workspace/` package：`onboarding_state.py` / `paths.py` / `skeleton.py` / `budget.py` / `template.py` / `validate.py` / `resolver.py` + `__init__.py` **全量 re-export**（35 public + 6 个被 test 直 import 的私有符号 + `_local_override_file_id`）。外部 `from octoagent.core.behavior_workspace import X` 零变化。`resolve_behavior_workspace`（357 行编排根）留 resolver。
  - 红线（O6/F6）：`@cache _load_behavior_template_text` **单一定义模块 + `__init__` re-export 同一函数对象**，禁重新包装（保全仓唯一缓存实例）；module-level 常量初始化与 import 顺序进对账清单显式锁定。
- **C2** behavior_workspace 新增写核收口（**实施形态：两段式 `prepare_behavior_file_write` + `commit_behavior_file_write`**，W1 双评审 Opus O2 确认优于原计划单函数——misc_tools proposal 门物理夹在 budget 与 write 之间，单函数装不下）——契约**显式收窄为写核**（O1/F2/F8 收敛）：`resolve_write_path_by_file_id` → `check_behavior_file_budget` → `mkdir(parents,exist_ok)` → **direct `write_text(content,"utf-8")`（保持非原子，禁改 tmp+replace）**，返回可翻译结果。**不含**事件发射、review_mode/proposal 门、onboarding marker、`invalidate_behavior_pack_cache`——这 4 项是 caller-specific 副作用，留各 caller（两处事件 payload `source` 不同：`"control_plane"` vs `"llm_tool"`，错误形态不同：`ControlPlaneActionError` vs `BehaviorWriteFileResult`）。`worker_service._handle_behavior_read_file/_write_file`（507-609）薄化为 adapter；`misc_tools.py` 仅 218→235→269-270 写核进对账，**248-324（proposal/marker/cache）不动**。加 golden response 对账测试（F8）。
- **C3** 残留扫描 + 对账清单 + wave 回归。
- 验证锚点：`test_behavior_workspace.py` 私有 import 零修改通过；`behavior.read_file`/`behavior.write_file` action 契约不变；两入口 golden response 字节级一致。

### W2：coordinator 瘦身 + D11（预计 3 commits）
- **C1** `_build_registry`（1335-1889，555 行纯声明）→ `action_registry.py`（自由函数 `build_action_registry()`）。**前置验证（O5）**：抽取前 grep 确认 1335-1889 体内零 `self.` 捕获（`definition()` 闭包已确认纯构造，但 555 行未全量验证）；若有 self 引用则参数化传入或降级为留主类。
- **C2** Telegram 适配（248-358）→ `telegram_command_parser.py`（registry 查询经参数传入）；簇 M `_ensure_default_main_agent_bootstrap`（1061-1177）评估移 `startup_bootstrap` 域（若耦合面大则留，wave 内决策并记录）。
- **C3** D11：`LLMWorkerAdapter` → `WorkerRuntimeAdapter`（orchestrator.py 2 处，零外部引用）。
- 编排根 `execute_action`/`get_snapshot`/`_dispatch_*` 全留。coordinator 1889→~1100。

### W3：setup_service 拆分（预计 3 commits）
- **C1** F 簇 review/risk 引擎（1701-2150+2223-2292，~520 行）→ `setup_review.py` `SetupReviewMixin`。
- **C2** E 簇 config/secret/env IO（~470 行）→ `setup_config_io.py` `SetupConfigIOMixin` + 4 个静态 → `setup_helpers.py`。
- **C3** D 簇 skill selection（1311-1434）→ `setup_skill_selection.py` mixin。
- 红线：`_cp_pkg` 间接引用（57/2383）不动；lazy import（2346/2457/2461）原位；编排根（`get_setup_governance_document`/`_handle_setup_apply`/`get_diagnostics_summary` 等）留主类。2576→~1300。

### W4：worker + session 拆分（预计 2 commits）
- **C1** worker：E 簇 6 静态 + 4 可静态化 → `worker_profile_helpers.py`；revision 链强 self（review/save/publish）→ `worker_profile_revision.py` mixin。lazy import 1366 原位。2100→~1300（W1 后基础上）。
- **C2** session：簇 D 静态纯函数 → `session_projection_helpers.py`（~150 行）。**刻意少动**（handlers 全是编排根）。1847→~1650。

### W5：capability_pack 拆分（预计 4 commits，风险最高的机械拆分）
- **C1** `BrowserSessionMixin`（1845-1964 + availability browser 分支耦合点处置记录）→ `capability_pack_browser.py`。`self._browser_sessions` 属性仍由主类 `__init__` 创建，mixin 方法经 self 访问——共享 dict 语义零变化（ToolDeps:1066 / `_pack_service=self`:1071 不动）。
- **C2** `WebSearchMixin`（1986-2106）+ `MediaInspectMixin`（tts 1966-1984 + file inspect 2108-2174）。
- **C3** `WorkerPlanMixin`（worker plan/spawn/split，909-1052 + 1228-1394，含测试直调的 `_launch_child_task`）。
- **C4** `ToolAvailabilityMixin`（1723-1838，测试直调 `_resolve_tool_availability*`）。
- **红线（F4/O4 实证清单）**：
  - `_launch_child_task` 被 `__get__` 描述符重绑（`test_capability_pack_phase_d.py:75`）+ `inspect.getsource` 源码断言（`test_phase_c_worker_to_worker.py:37`）→ **必须留 instance-method 形态**（禁 staticmethod 化、禁抽自由函数），mixin MRO 可达 + 源码可读。
  - `_is_ddg_anomaly_page` / `_parse_duckduckgo_results` 被**类级直调**（`test_capability_pack_web_search.py:35/40/45/51/57`）→ WebSearchMixin 中**保持 staticmethod descriptor 类型不变**，经 `CapabilityPackService.` 类访问可达。
  - `_mcp_tool_enabled_by_default`（`test_capability_pack_tools.py` 8 处）、`_resolve_tool_availability*`（`test_graph_pipeline_security.py` + 2 e2e_live）、`_fetch_browser_page`（`test_e2e_ssrf_guard.py:90`）、`_browser_sessions` 属性覆写（`test_graph_pipeline_security.py:297/322/340`）→ 全部经 mixin 继承保持 `self.`/类访问。
- 验证锚点：上述 7 个测试文件**零修改**通过。2174→~1100-1300。

### W6：D9 跨层归位 + living-docs（预计 2 commits）
- **C1** `_ApprovalOverrideMemoryCache`（cap_pack:138-185）下沉 `packages/tooling`（approval 域），cap_pack import + re-export 兜底。
- **C2** living-docs 漂移闸：`docs/codebase-architecture/harness-and-context.md` + `docs/blueprint/module-design.md` 同步三层职责定调（broker=执行运行时+registry SoT / cap_pack=治理面+pack 投影 / harness=纯 wiring）+ 截断/错误包装/双 safety-scan 现状文档化 + Manus 稳定排序/az-1 扩展缝记为设计原则。**harness 代码零改动**。

### W7：F118 D8 typed DI（预计 2 commits）
- **C1** `ControlPlaneContext.service_registry: dict[str,Any]` → typed registry（9 typed 字段，构造后一次性 set）；`_get_service` 9 处调用 → typed accessor。**错误语义字节级等价**（裸 RuntimeError + 同 message，先补错误路径单测锁定再改）。
  - **字段类型 = concrete service 类**（O3）：`setup_service.py:995/1007/1106` 调用解析出 service 的**私有方法**（`._handle_policy_profile_select`/`._handle_agent_profile_save`/`._handle_provider_oauth_openai_codex`）——窄化为 public Protocol 会断这 3 处；收敛私有调用为 public 契约 = 接口变更超 F108 红线。
  - **保留迭代能力（F1）**：`automation_service.py:290` 以 `service_registry.values()` 遍历汇总全部 `action_routes()` 做 `automation.create` 的 action_id 存在性校验——typed registry 须提供 `all_services()` 等价物，并加 `automation.create` 有效/无效 action 回归测试。
- **C2** 7 处跨 service 属性赋值（_coordinator.py:211-221）→ 显式 typed setter（target service 声明 typed Optional 字段 + fail-fast accessor）；`bind_*` 3 方法保留（ControlPlane↔AutomationScheduler 真循环依赖，impact-report §三）。
  - **验证项（Opus）**：fail-fast 时机前移（运行期 AttributeError → 构造/绑定期）不改 happy-path；harness startup 1352/1355/1359 bind 顺序下不提前触发。
- 红线：① `monkeypatch.setattr(control_plane_module, ...)` 测试模式（`__init__.py:7`）不破坏；② **instance 私有属性兼容（F5）**——`test_control_plane_api.py:1990-1993/2125-2137/2275-2285` 直接 monkeypatch/读取 `control_plane_service._mcp_service` / `._proxy_manager`，禁改名/隐藏。

### W8：顺手项（预计 3-4 commits，**归属修正 O2**：render 非幂等 / assert / research-handoff 是 **F124** 其余 LOW，scan_context docstring 是 F125 LOW）
- **C1**（零变更类）F124/F125 遗留 LOWs：`assert len>=15` → 显式 raise（-O 安全，threat_scanner:468）；scan_context docstring 修正；render 幂等性评估（若修复有行为面则记录并最小化）；**research handoff 现场 new service**（`agent_context_prompt_assembly.py:446` `_build_research_handoff_block` 内每次 `ContentThreatScanService()` 现场构造 → 复用注入/单例；动之前先读 `test_tool_result_threat_scan.py` no-bypass 断言确认不变量）。
- **C2**（**显式行为变更，独立 commit，不进字节对账，可单独 bisect/revert——O7 硬约束**）AmbientRuntime 秒级时间戳挪出冻结前缀：从 core_sections（第一条 system message）挪到非冻结区或降粒度，commit message 显式标注行为变更 + 单独验证（prefix-cache 命中改善为目标，LLM 可见内容位置变化为代价）。**归属待人裁（§3.5 分歧 2）**：Codex F7 建议整体 spin out；Opus O7 认为独立 commit 即可。
- **C3** completion-report + handoff（对照本计划标"实际 vs 计划"）。

## 3. F108 是否再拆（GATE 决策点 1）

**结论：建议拆。** ~12,300 行实动面、8 waves、约 21 commits、每 wave 双评审——单 Feature 的 completion-report/verify 制品与 review 焦点都会爆炸（F101 范围爆炸 10 轮 review 的教训）。

**推荐方案 A：拆 2 个 Feature，按"域内机械拆分 vs 跨层契约"切**
- **F108a 域内拆分**（W1-W5）：全是"单文件/单域内重组"，纯机械搬运，字节级对账全覆盖，验证范式统一。
- **F108b 跨层收口**（W6-W8）：跨层移动 + typed DI + 显式行为变更顺手项 + living-docs——review 焦点是"契约语义"而非"搬运保真"。
- 收益：F108a 全合 master 后 F108b 基线干净；两份独立 completion-report；review 心智单一。成本：多一套 spec 制品（~小时级）。

**备选 B：不拆，单 F108 8 waves**——每 wave 仍独立合 master，省一套制品，但最终 verify/completion-report 跨 21 commits，焦点弱。

**备选 C：拆 3+（per-service Feature）**——制品 overhead 过高，不推荐。

双评审意见：Opus **支持方案 A** 并否决 C（要求 F108b 内 W7"typed 化保语义"与 W8 C2"故意改行为"commit 边界清晰 + fail-fast 时机前移单独立论）；Codex 倾向 W8 整体从 F108b 再剥出（见 §3.5 分歧 2）。

## 3.5 双评审分歧（人裁结果，2026-06-12）

1. **D12 收口范围语义**（Opus O1 HIGH，Codex F2/F8 同向）：已按收敛结论修订 W1 C2（写核收口、4 项副作用留 caller、保持非原子 write_text）。两席位结论同向，已闭环。
2. **W8 C2 AmbientRuntime 归属**（Codex F7 vs Opus O7）：**用户裁定采 Opus 方案**——折入 F108b 独立 commit，附 Codex 缓解（显式行为变更标注 + 排除出零变更验证矩阵 + completion-report 单列 + 可单独 bisect/revert）。
3. **F118 typed 字段粒度**（Opus O3）：concrete service 类（零行为变更优先），已写入 W7 C1；收敛私有调用为 public 契约不在 F108 做。

## 3.6 baseline 账本（2026-06-12，d6148903 @ F108 worktree，PYTHONPATH 锁定）

- **全量**：`4091 passed / 6 failed / 13 skipped / 1 xfailed / 1 xpassed`，40:33（4112 collected）。
- **6 个 failed 全部为 e2e_live 真实 LLM 测试**（`test_e2e_smoke_real_llm` domain 1/2/3 / `test_e2e_mcp_skill_pipeline` domain 6/7 / `test_e2e_delegation_a2a` domain 8），失败模式一致：`TimeoutError: task ... 在 180.0s 内未达终态；最后 status='running'`——credentials 存在但 provider 调用挂起（环境性，疑 openai-codex 侧），**与代码无关**。
- **e2e_smoke 单独跑：8/8 passed in 2.5s**（hook 同款命令）——commit 门正常。
- **每 wave 回归门定义**：全量回归中上述 6 个测试按名单单独记账（同环境同结果=非回归；provider 恢复转 pass=改善）；其余 4091+ 0 regression；e2e_smoke 8/8 必过。

## 4. 验证矩阵（每 wave）

| 检查 | 命令/方法 | 门槛 |
|------|----------|------|
| 全量回归 | `PYTHONPATH=<worktree src 锁定> uv run --no-sync python -m pytest -q` | 0 regression vs baseline（4112 collected；baseline passed 数以首跑为准，flaky 单独 rerun 复核） |
| e2e_smoke | pre-commit hook（180s watchdog） | 8/8 |
| 字节级对账 | 搬运块 extract-diff 清单 | 唯一豁免逐条记录 |
| 残留扫描 | 旧符号全仓 grep | 0（豁免显式归档） |
| 双评审 | Codex adversarial + Opus 第二模型 | 0 HIGH 残留，分歧人裁 |
| living-docs | W6/W8 漂移闸 | harness-and-context.md / module-design.md 同步 |

## 5. 回滚策略

每 wave = 独立 commit 链，合入前仅存在于 feature 分支；wave 内验证失败 → `git reset` 该 wave；已合 master 的 wave 出问题 → revert commit 链（每 wave 自洽可独立 revert，无跨 wave 数据迁移）。全程禁 force push。

## 6. 范围外（明确不做）

- harness 11 段结构调整（hermetic 测试钉死；只文档）
- broker execute 主链路任何改动（schema 校验 spin out）
- 截断/威胁扫描策略统一（行为变更，文档化现状）
- tool_call_id 配对 eviction / artifact read-back / per-turn 预算（spin out 独立 Feature）
- D2 WorkerProfile/AgentProfile 合并（F117）、control_plane domain service 隐性耦合中 D8 以外部分
