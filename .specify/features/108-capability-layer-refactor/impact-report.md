# F108 Capability Layer Refactor — 影响分析报告（Phase 1）

> 基线：origin/master `d6148903`。worktree：`feature/108-capability-layer-refactor`。
> 方法：4 路并行 recon agent（very thorough）逐文件通读 + caller/test grep，主 session 综合（执行分发、决策集中）。
> 原始数据：`recon/recon-A-d9-three-layers.md`（D9 三层）、`recon/recon-B-setup-worker-service.md`、`recon/recon-C-coordinator-session-behavior.md`、`recon/recon-D-f118-d11-d12-design-inputs.md`。
> 偏离说明：spec-driver refactor 模板面向"重命名/迁移"型单目标重构；F108 是多子重构程序（program），本报告按子重构维度组织，批次规划见 `refactor-plan.md`。

## 重构目标

- 目标：D9 三层职责厘清 + F121 巨型 domain service 二次拆分 + F118 control_plane D8 解耦 + D11/D12 顺手
- 类型：multi-concept（8 个主文件 + 跨层契约）
- 约束：**行为零变更**（除显式标注的 1 项行为变更顺手项），F113 字节级对账范式

## 影响范围总览

| 维度 | 数值 |
|------|------|
| 主重构面 | 8 文件 14,488 行（setup 2576 / cap_pack 2174 / worker 2100 / coordinator 1889 / session 1847 / behavior_workspace 1741 / harness 1388 / broker 773） |
| 实际可动面 | ~12,300 行（**harness 1388 结构不动**——6 个 `_bootstrap_*` 符号被测试直调/源码断言钉住 + main.py 唯一 caller + 纯 wiring 拆分 ROI 低；**broker 773 基本不动**——仅 import 级调整） |
| 直接 caller 文件 | ~25（含 main.py / deps.py / coordinator / builtin_tools / provider.dx） |
| 测试触及文件 | ~40（其中 **测试直调私有方法** 集中在 cap_pack 7 文件 / harness 4 文件 / broker 2 文件 / behavior_workspace 1 文件） |
| 跨包引用 | **是**（packages/core + packages/tooling + apps/gateway 三包） |
| 风险评级 | **high**（影响文件 31–100 区间 + 跨包；未达 critical——每个子重构的 caller 面实测远小于担忧，见下） |

## 一、D9 三层职责的真实重叠点（具体 file:line）

> 完整版见 recon-A。简称 broker=`packages/tooling/.../broker.py`，harness=`.../harness/octo_harness.py`，cap_pack=`.../services/capability_pack.py`。

| # | 重叠关注点 | 各层位置 | 判定 |
|---|-----------|---------|------|
| A | 工具 schema 注册/解析 | 产生（reflect）：cap_pack:1085；存储（registry SoT）：broker:160-276；二次索引（ToolIndex）：cap_pack:311-354 | 同一工具元数据两层各持一份表示。**收口方向**：文档化 broker=SoT、cap_pack=pack 投影；不动行为 |
| B | 权限/审批 override 缓存 | 缓存实现类：cap_pack:138-185（`_ApprovalOverrideMemoryCache`）；构造+共享：harness:608-634 + 781-788；执行点：broker:370-392 | 同一实例三层共持（有意 wiring）。**可动作**：`_ApprovalOverrideMemoryCache` 类下沉 tooling 层（纯位置归位） |
| C | ApprovalGate | 构造：harness:864-928；持有/分发：cap_pack:275-283 + 1074 | cap_pack 是 late-binding 中转站，结构上成立，文档化即可 |
| D | 结果截断 | 通用：broker after-hook 链 496-520 + harness 装配 635-642；cap_pack 内自截：1871（html 500k 硬截）、1958（_truncate_text）、459 | 分散但**统一策略=行为变更**，F108 仅文档化现状 |
| E | 威胁扫描 | 内容扫描：broker:62-109/687-724；出站 SSRF：cap_pack:73-77/1864/2017 | 两类 safety scan 语义本就不同，**不算债**，文档澄清 |
| F | 工具执行编排 | 单工具执行：broker:314-535；工具集挂载编排：cap_pack:558-825；可用性裁决依赖运行时状态：cap_pack:1723-1773 | 理论正交；真正问题是 cap_pack 超载（见下） |
| G | 错误包装 | 产生：cap_pack 业务方法多处 raise；包装：broker:471-493 兜底 | 隐式契约耦合，文档化 |

**D9 结论（重要纠偏）**：三层 import 方向干净（broker 零对上依赖；cap_pack/harness 高→低）。**D9 的实质不是"层次倒置"而是 cap_pack 超载**——它同时是工具注册聚合器 + 治理面 + **builtin tool 业务逻辑宿主**（browser/tts/web search/file inspect，1845-2174 共 ~330 行 + 散落耦合 ~17 行）。主修复动作 = cap_pack 业务逻辑按 mixin 拆出（测试直调面实证清单——Codex F4 修订：`_fetch_browser_page`/`_resolve_tool_availability*`/`_launch_child_task`（含 `__get__` 重绑 + `inspect.getsource` 断言）/`_mcp_tool_enabled_by_default`/`_is_ddg_anomaly_page`/`_parse_duckduckgo_results`（类级 staticmethod 直调）/`_browser_sessions` 属性覆写 → **必须 mixin 继承 + descriptor 类型不变**，F113 约束）+ 三层职责文档化。**harness：6 个 `_bootstrap_*` 符号被测试钉住（`test_hermetic_isolation.py` 直调 `_bootstrap_paths`/`_bootstrap_stores`/`_bootstrap_tool_registry_and_snapshot`/`_bootstrap_owner_profile`/`_bootstrap_runtime_services` + `inspect.getsource(_bootstrap_executors)`:254；Codex F3 修正——原"11 段逐段直调"表述夸大，basic_tool_context/routine 的引用实为注释/断言消息）→ 决策不变：harness 结构不动。**

## 二、每个巨型 service 的职责簇 + 拆分方案

> 完整簇表见 recon-B/recon-C。下表为拆分决策摘要。

| 文件 | 职责簇 | 编排根（留主类） | 可拆出 | 预估瘦身 |
|------|--------|----------------|--------|---------|
| **setup_service** 2576 | 7 簇：路由/文档构建 B/handlers C/skill selection D/config·secret·env IO E/review·risk 引擎 F/runtime·wizard·diagnostics G | `get_setup_governance_document`(355)、`_handle_setup_apply`(883)、`_build_setup_review_summary`(1701, 412 行 F 簇内编排根)、`get_diagnostics_summary`(601) | F 簇→`SetupReviewMixin`（~520）；E 簇→`SetupConfigIOMixin`+静态 helpers（~470）；D 簇→`SetupSkillSelectionMixin`（~124）；4 静态→helpers | →~1300 |
| **worker_service** 2100 | 6 簇：路由/文档 B/profile 生命周期 C/**behavior IO C2（域泄漏）**/cap_pack 副本 D/域 helpers E | `get_worker_profiles_document`(105)、`_handle_worker_profile_apply`(898)、`_review_worker_profile_draft`(1723, 243 行) | **C2（507-609）剥离**→behavior 域收口（见 D12）；E 簇 6 静态+4 轻 self→`worker_profile_helpers.py`；E 强 self→`WorkerProfileRevisionMixin` | →~1300 |
| **capability_pack** 2174 | 注册聚合/治理面/**4 块业务逻辑宿主**（browser ~137、web search ~120、tts ~19、file inspect ~67）+ worker plan/spawn（~166） | `select_tools`/`resolve_profile_first_tools`(558-825)、`startup`/`refresh`(294-389)、`build_tool_context`(425) | `BrowserSessionMixin`/`WebSearchMixin`/`MediaInspectMixin`/`WorkerPlanMixin`/`ToolAvailabilityMixin`（全部 mixin——测试直调面最大）；`_ApprovalOverrideMemoryCache`→tooling | →~1100-1300 |
| **behavior_workspace** 1741 | 53 自由函数 + 6 数据类，无可变全局（仅 1 个 @cache）；7 个天然模块簇 | `resolve_behavior_workspace`(1015, ~357 行) 留 resolver 模块作公共入口 | 拆 package：`onboarding_state`/`paths`/`skeleton`/`budget`/`template`/`validate`/`resolver` + **`__init__` 全量 re-export**（6 私有符号被 test 直 import + `_local_override_file_id` 被 behavior_commands.py import） | 1741→7×~250 |
| **_coordinator** 1889 | 14 簇；`_build_registry`(1335-1889) **555 行纯声明式 action 定义** | `execute_action`(364)、`get_snapshot`(758)、`_dispatch_*`(419/430) | `_build_registry`→`action_registry.py`；telegram 适配(248-358, ~111)→`telegram_command_parser.py`；簇 M bootstrap(1061-1177) 评估移 startup_bootstrap 域 | →~1100 |
| **session_service** 1847 | 6 簇；handlers 全是编排根 | `get_session_projection`(122)、`_build_session_projection_items`(428)、全部 `_handle_session_*`(1166-1847) | 簇 D 静态纯函数→`session_projection_helpers.py`（~150）；可拆面有限（大部分是编排根）——**刻意少动** | →~1600 |

**横向约束（全 wave 适用）**：① setup/worker/session/coordinator **零测试私有直调**（黑盒 API 测试覆盖）→ 拆分自由度高；② lazy import 不可重排（setup 3 处 + `_cp_pkg` monkeypatch 间接引用 57/2383、worker 1366、coordinator 11 处、session 2 处）；③ 唯一生产 caller 都是 coordinator（setup/worker/session）或 main.py（harness）→ caller 面极小。

## 三、F118 D8 解耦的真实形态

- **"14 处 bind_* setattr"实测口径**：coordinator 3 个 `bind_*` 定义（`_coordinator.py:210/214/219`）+ **7 处跨 service 直接属性赋值**（211-221，`self._X_service._attr = ...`）+ `service_registry` 9-key 延迟回填（166-176）。harness 调用点 1352/1355/1359。
- **`_get_service` 字符串查找**：定义 `_base.py:98`；**9 个调用点**（setup 8 处 + mcp 1 处），查询集合仅 `{"setup","agent","mcp"}`；失败抛**裸 RuntimeError**（落 ACTION_EXECUTION_FAILED 而非 REJECTED——typed 化必须保留此可观测语义）。其中 3 处调用解析出 service 的**私有方法**（setup_service.py:995/1007/1106）→ typed 字段须为 concrete 类（Opus O3）。
- **registry 的第 10 种用法（Codex F1）**：`automation_service.py:290` 直接遍历 `service_registry.values()` 汇总全部 `action_routes()` 做 `automation.create` 的 action_id 存在性校验——typed registry 须保留 `all_services()` 迭代等价物。
- **硬障碍**：ControlPlane ↔ AutomationScheduler **真循环依赖**（`octo_harness.py:1329-1352`）——scheduler 构造需要 control_plane 实例，scheduler 又要绑回。⇒ **bind_automation_scheduler 无法消除**，只能 typed 化。proxy_manager/mcp_installer 是 startup-late 资源（非严格循环）。
- **可行方案**（行为零变更）：`service_registry: dict[str,Any]` → typed registry 对象（9 typed 字段一次性 set）；`_get_service(name)` 9 处 → typed accessor（错误信息/类型字节级等价）；7 处跨 service 属性赋值 → 显式 typed setter（断链从运行期 AttributeError 前移到构造/绑定期类型检查）。bind_* 3 个方法保留（循环依赖是真实约束）。
- **测试无阻**：无 conftest fixture 依赖 bind_*/setattr 路径；`test_telegram_service.py` 直构 ControlPlaneService 不触发 bind 链。

## 四、D11 / D12 实测

- **D11 LLMWorkerAdapter**：`orchestrator.py:341` 定义 + 431 唯一实例化，**零测试引用零跨包引用**——改名波及 1 文件 2 处。实际职责：WorkerRuntime 通用调度薄包装（非仅 LLM）。建议名 `WorkerRuntimeAdapter`。
- **D12 BehaviorFileRegistry**：**该类不存在**（审计名失真）。真实债 = behavior 文件**写入序列重复**——但 Opus O1 实测修正：两处**仅写核同构、下游副作用各异**。可收口窄核 = `resolve_write_path_by_file_id`→`check_behavior_file_budget`→`mkdir`→direct `write_text`（两处都非原子，收口不改原子性）；4 项 caller-specific 差异**留各 caller**：misc_tools 有 review_mode/proposal 门（248-265）+ onboarding marker（291-306）+ `invalidate_behavior_pack_cache`（307-312），事件 payload `source` 字段两处不同（`"control_plane"` vs `"llm_tool"`），返回类型不同。收口 = behavior_workspace 新增窄核 `write_behavior_file_content()`，与 worker_service C2 剥离同 wave 处理。

## 五、设计输入处置（现状定位 → 建议）

| 设计输入 | 现状（recon-D） | 处置建议 |
|---------|----------------|---------|
| AmbientRuntime 秒级时间戳挪出缓存前缀 | 秒级 timestamp（`agent_context_helpers.py:219/221`）位于**第一条 system message 冻结前缀中段**（`agent_context_prompt_assembly.py:157-169` core_sections→361）——每秒破坏 prefix cache | **顺手折入**但作为**显式行为变更 commit**（prompt 布局变化，不进字节对账），放最后 wave 单独验证 |
| 执行前 schema 校验 + 结构化 retry feedback | execute 路径**完全无** schema 校验（broker.py:314-531；schema 仅用于工具描述）；retry feedback 机制存在但无字段级结构化反馈 | **spin out 独立 Feature**（动 broker execute 主链路 + 新行为；与零变更冲突） |
| tool_call_id 确定性 tail eviction | `ConversationTurn` 模型**无 tool_call_id 字段**（context_compaction.py:119-127，全文件 grep 零命中）——配对保护需模型字段扩展 | **spin out**（模型/存储变更 + 行为变更） |
| 大输出 artifact read-back + per-turn 预算 | 卸载已存在（`hooks_legacy.py:141 LargeOutputHandler`→artifact_store + runner.py:684 第二层截断）；缺 read-back 工具与 per-turn 预算 | **spin out**（能力新增） |
| Manus 工具集稳定排序/policy-deny 可见性 | — | D9 文档化为设计原则，F108 不实现 |
| az-1 决策环具名扩展缝 | BeforeHook/AfterHook 抽象已在（broker:282-308） | D9 文档化，无代码动作 |

## 六、风险清单

| 风险 | 等级 | 缓解 |
|------|------|------|
| cap_pack 测试直调私有方法面大（7 个测试文件） | 高 | 全部 mixin 继承（方法仍在类上）；`_browser_sessions` dict 共享引用语义逐字节保持（ToolDeps:1066 传引用 + `_pack_service=self`:1071 回调不动） |
| lazy import 搬运重排破坏行为（F113 教训） | 高 | 禁 ruff I001 --fix；lazy import 原位保留；字节级对账 |
| `_cp_pkg` monkeypatch 间接引用被"优化"成直接 import | 中 | 显式列入每 wave 检查单（setup_service.py:57/2383） |
| behavior_workspace 拆包破坏 6 个测试直 import 私有符号 | 中 | package `__init__` 全量 re-export（含私有符号），import 路径零变化 |
| F118 错误语义漂移（RuntimeError→其他） | 中 | typed accessor 抛同类型同 message；错误路径单测锁定后再改 |
| W7 隐性兼容面：测试 monkeypatch `control_plane_service._mcp_service`/`._proxy_manager` 实例私有属性（test_control_plane_api.py:1990/2125/2275，Codex F5） | 中 | typed setter 不改名不隐藏这些属性 |
| 同文件多 wave 冲突（worker_service 被 Wave 1 与 Wave 4 都触碰；coordinator 被 Wave 2 与 Wave 7 触碰） | 中 | wave 严格串行合入 master，后 wave rebase |
| 全量回归 baseline 有已知 flaky（task_runner race，F083 工程债） | 低 | baseline/改后同机同命令对比；flaky 单独 rerun 验证 |

## 七、Phase 1 结论

1. **F108 可做且值得做**，但 ~12,300 行实动面**一个 Feature 一把梭 review 必然失焦**——拆分建议见 refactor-plan.md §"是否再拆"（推荐拆 F108a/F108b）。
2. 三个"原计划要大动"的点实测**应当少动或不动**：harness（hermetic 钉死）、broker（最低层最干净）、截断/威胁扫描统一（=行为变更，出范围）。
3. 两个"原计划没强调"的点实测是**最高 ROI 切入**：cap_pack 业务逻辑宿主拆分（D9 实质）+ behavior_workspace 拆包（含 D12 收口 + worker C2 域泄漏回迁）。
