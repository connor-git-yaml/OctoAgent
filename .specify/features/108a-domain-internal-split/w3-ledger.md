# W3 对账清单（setup_service 三 mixin 拆分）

> AC-3 制品。双评审：Codex **safe to commit 0 finding** + Opus **通过 0 HIGH/0 MED**（3 记账项）。

## 搬运对账

- **方法级**：43 方法（原文件全量）→ 4 文件，`check_method_move.py` **0 MISSING/DIFF/DUP/NEW**（双席独立复跑一致）；25 留守方法 changed_count=0（Codex 逐字节核）。
- setup_service.py **2576→1520**（+10/−1066）；新文件：setup_review.py 561（F 簇 4 方法）/ setup_config_io.py 436（E 簇 9）/ setup_skill_selection.py 154（D 簇 5）。
- MRO：`SetupDomainService(SetupReviewMixin, SetupConfigIOMixin, SetupSkillSelectionMixin, DomainServiceBase)`；3 mixin 零 super() 调用；跨类 self 调用全可达（含 D 簇→主类 `get_skill_governance_document` 反向调用，Opus 实测焦点链路覆盖）。

## 豁免记录

| # | 豁免项 | 理由 |
|---|--------|------|
| 1 | 主文件移除 12 个随方法搬走的顶层 import（含补移的 `Project`）| 每个移除前 AST 0 引用断言；`Project` 首轮被中文字符串字面量 grep 误匹配漏掉（Opus 抓出"搬运副产物"），按同规则补移 |
| 2 | 3 个 pre-existing dead import（`McpProviderCatalogDocument`/`OwnerProfileDocument`/`PolicyProfilesDocument`，HEAD 即 import-only）保留 | 非 W3 产物，零变更 wave 不夹带无关清理（双席同向）；归 cleanup 顺手项 |
| 3 | 簇头注释随簇走 / class 头 4-mixin 化 / 行号位移 | 结构必然 |
| 4 | ruff I001 un-sorted 预期态 | 方法论 §0.2 禁排序（Opus O9 确认设计意图）|

## 实施偏离

1. **setup_helpers.py 未建**（计划 C2 字面）：statics 随簇走/留主类（`_format_config_validation_errors`→config_io，`_map_update_source`/`_dedupe_resource_refs`/`_deep_merge_dicts` 留主类）——保调用点字节零变化，主 session 派单时的明确指令，计划文字滞后于此决策。**裁定接受**。
2. **主文件 1520 vs 预估 ~1300/1450**：估算误差（helpers 未独立抽出），43 方法对账证零遗漏。接受。
3. `test_setup_governance.py` 不存在（计划假设错误），焦点改用 test_control_plane_api + test_bootstrap_simplification。
4. recon-B 假设边修正：`_build_setup_review_summary` 实测**不调** `self._credential_store`（Codex 核实），F→E 跨 mixin 边不存在（同类无影响）。

## 红线复核（双席全过）

- `_cp_pkg` import + `RuntimeActivationService` 引用与 HEAD 字节一致（仅行号位移）
- lazy import：`_collect_bridge_refs` 内 `ProjectBindingType` 随方法体进 config_io 函数体同位置；`_get_wizard_session` 2 处留主文件原位
- 编排根 4 方法（get_setup_governance_document/_handle_setup_apply/_handle_setup_review/get_diagnostics_summary）AST 提取逐字节零变化
- mixin import 完整性：AST Name Load 检查（含字符串注解/装饰器）无 unresolved（Codex）

## 验证

- wave 回归门：**4105 passed / 0 failed**（= W2 v2 基线持平）/ 13 skipped / 6 deselected / 4:08
- 焦点：79 passed（双席各自跑）+ `Project` 补移后 74 passed 复验
- e2e_smoke：commit hook 自动

## 双评审 finding 闭环表

| Finding | 处置 |
|---------|------|
| Codex：0 finding，safe to commit | — |
| Opus 人裁 1：setup_helpers.py 未抽 | 接受（偏离 #1），refactor-plan 文字同步 |
| Opus 人裁 2：1520 行规模偏差 | 接受（偏离 #2）|
| Opus 人裁 3：dead import 处置 | `Project` 补移（豁免 #1）；3 个 pre-existing 留 cleanup（豁免 #2）|

**0 HIGH 残留。**
