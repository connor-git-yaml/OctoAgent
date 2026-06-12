# F108 计划双评审 — Opus 席位（第二模型）

> 评审对象：impact-report.md + refactor-plan.md + recon ×4。基线 d6148903。
> 方法：独立抽查代码验证（非仅读制品）。所有 file:line 经评审者本人复核。
> 结论：制品 line-level 精度优秀、方法论合规、wave 自洽性成立；**O1(HIGH) + O2(MED) 进实现前必须闭环**；O3/O4/O5 补红线；O6/O7/O8 确认计划正确。

## Finding 列表

### O1 — D12 写序列"字节级一致/同构可收口"被高估【HIGH】
`worker_service.py:550-609` vs `misc_tools.py:192-324` 实测**不同构**：

| 步骤 | worker_service:550-609 | misc_tools:192-324 |
|---|---|---|
| review_mode / proposal 门 | 无 | 有（248-265，REVIEW_REQUIRED && !confirmed → proposal） |
| BOOTSTRAP.md onboarding marker | 无 | 有（291-306 `mark_onboarding_completed`） |
| `invalidate_behavior_pack_cache` | 无 | 有（307-312） |
| structlog `source` 字段 | `"control_plane"` | `"llm_tool"` |
| logger 名 | `"control_plane.behavior"` | `"behavior.write_file"` |
| 返回类型 | `ActionResultEnvelope` | `BehaviorWriteFileResult` |
| `bytes_written` | 无 | 有（318） |

真正可收口**仅窄核**：`resolve_write_path_by_file_id` → `check_behavior_file_budget` → `mkdir(parents,exist_ok)` → `write_text(content,"utf-8")`。事件 payload **非**字节级一致（source 不同）。
附带纠正：两处都是**非原子 write_text**（behavior_workspace 的 mkstemp+os.replace 原子机制仅 `save_onboarding_state` 用，215-222）——收口不改原子性。
**处置**：`write_behavior_file_content()` 契约显式收窄为窄核；事件发射、proposal 门、onboarding marker、cache invalidation 4 项 caller-specific 留各 caller；计划文字改"写核同构、下游副作用各异"；misc_tools 对账边界标注"仅 218→235→269-270 进对账，248-324 不动"。

### O2 — W8 漏掉 CLAUDE.local.md 承诺的「research handoff 现场 new service」LOW【MED】
`agent_context_prompt_assembly.py:446`：`_build_research_handoff_block` 内每次调用现场 `ContentThreatScanService()`。CLAUDE.local.md:349 明文归 F108 顺手，计划漏列。另：计划把这些 LOW 统称"F125 遗留"，实际 render/assert/handoff 归 **F124** 其余 LOW，F125 自己的 LOW 是 scan_context docstring——归属张冠李戴（traceability 失真）。
**处置**：W8 C1 补入该项（动之前先读 `test_tool_result_threat_scan.py` no-bypass 断言）；修正 LOW 归属标注。

### O3 — F118 typed accessor 须保留 concrete 类型【MED】
9 个 `_get_service` 站点中 **3 个调用解析出 service 的私有方法**：`setup_service.py:995` `._handle_policy_profile_select` / `:1007` `._handle_agent_profile_save` / `:1106` `._handle_provider_oauth_openai_codex`。typed registry 字段若窄化为 public Protocol 会断这 3 处。
**处置**：typed 字段声明为 concrete service 类（零行为变更优先）；若想收敛跨 service 私有调用为 public 契约 = 接口变更，超 F108 红线应 spin out。

### O4 — `_launch_child_task` 测试用 `__get__` 描述符重绑【MED】
`test_capability_pack_phase_d.py:75`：`CapabilityPackService._launch_child_task.__get__(cap, CapabilityPackService)`。mixin 继承下 MRO 解析仍成立（计划结论正确），但**禁 staticmethod 化、禁抽 helper、必须留 instance-method 形态**——须在 W5 红线显式点名。

### O5 — W2 `_build_registry` 抽自由函数前提未声明【LOW→MED】
`definition()` 闭包工厂确认纯构造（无 I/O/state），但 555 行体内是否零 `self.` 捕获未全量确认。
**处置**：W2 C1 前置验证 `grep -n "self\." | awk '1335<=$1 && $1<=1889'` 零命中；列入计划前置项。

### O6 — W1 behavior_workspace 拆包风险复核【确认项，零风险】
- `@cache _load_behavior_template_text`(1495)：package `__init__` re-export 同一函数对象 ⇒ 全仓唯一 cache wrapper。**无双实例风险**。红线：`__init__` 禁重新包装。
- 私有符号：test 6 个（test_behavior_workspace.py:10-19）+ `behavior_commands.py:18` `_local_override_file_id`（6 处使用 650-716）——`__init__` 全量 re-export 后完全等价。计划已覆盖。

### O7 — W8 C1（零变更）与 C2（行为变更）须独立 commit【LOW，强化】
保证 C2 可单独 bisect/revert。计划已说 commit message 标注，强化为"不同 commit"硬约束。

### O8 — 同文件多 wave 串行依赖复核【确认项，成立】
W1∩W4（worker：507-609 vs 611-2100 行段不重叠）、W2∩W7（coordinator：1335-1889/248-358 vs 166-176/210-221 不重叠）、W5∩W6——全部成立，rebase 冲突面为零，中间态不破坏调用方。

## 拆分建议评估
**支持方案 A（拆 F108a/F108b）**，否决 C。两点强化：① F108b 内 W7（typed 化保语义）与 W8 C2（故意改行为）是两种 review 焦点，commit 边界必须清晰；② F108b spec 显式验证"W7 typed setter fail-fast 时机前移不改 happy-path、harness startup 1352/1355/1359 bind 顺序下不提前触发"。

## 必须人裁的分歧候选
1. **O1**：D12 收口范围语义定性（写核收口、副作用留 caller）——若 Codex 就"能否收口"给不同结论，此处是主要分歧面。
2. **O2**：research-handoff LOW 补入 W8 还是显式另立（不能静默 drop）。
3. **O3**：typed registry 字段粒度 concrete vs Protocol（评审者倾向 concrete，零行为变更优先）。
