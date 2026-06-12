# W2 对账清单（coordinator 瘦身 + D11）— 字节级对账 + 豁免记录

> AC-3 制品。双评审：Codex（1 HIGH + 2 LOW）+ Opus（PASS，0 HIGH/0 MED + 1 LOW 文档项）全闭环。

## 搬运对账（Opus 独立 md5 复核双一致）

| 块 | 原位置（HEAD _coordinator.py） | 新位置 | 对账 |
|---|---|---|---|
| `_build_registry` body（554 行，92 ActionDefinition + 1 capability） | 1336-1889 | `action_registry.py:21-574` `build_action_registry()` | 去 4 空格归一化 diff 空；md5 一致；构建顺序 SHA256 一致（Codex INFO）|
| Telegram 块（111 行，`build_telegram_action_request` + `_has_telegram_alias`） | 248-358 | `telegram_commands.py:23-133` `TelegramCommandMixin` | 逐字节相同（缩进级不变）；md5 一致 |

coordinator 净改动 **+4 / −670**（2 import + class 基类 + 调用点；1889→1213 行）。4 行新增全部列举无隐藏编辑（Opus 验证）。

## 豁免记录

| # | 豁免项 | 理由 |
|---|--------|------|
| 1 | `build_action_registry` def 行改名 + 整体去 4 空格缩进级 | 方法→模块级函数的结构必然（O5 前置已验证零 self 捕获）；归一化 diff 空为机械证明 |
| 2 | 移除 coordinator 2 个失效 import（`ControlPlaneCapability`/`ControlPlaneSupportStatus`） | 仅 `_build_registry` 使用（移除前断言全文件恰 1 处引用）；随块迁入 action_registry |
| 3 | 2 处节注释（244-246/1331-1333 分节横线）随段删除 | 出处由新模块 docstring 承载 |
| 4 | provenance 注记含旧名（orchestrator.py:344 docstring `原名 LLMWorkerAdapter` / octoagent-architecture.md:283 `前名 LLMWorkerAdapter`） | 仓库 Feature 溯源注释惯例（Opus O3 对照 execution_context.py 确认合规）；Codex LOW 按此豁免 |

## 实施偏离（均经评审确认优于计划字面）

1. **C2 形态**：计划 `telegram_command_parser.py`（自由函数 + registry 参数传入）→ 实施 `telegram_commands.py` + `TelegramCommandMixin`（`self.get_action_definition` 经 MRO）。理由：测试 `control_plane.build_telegram_action_request(...)` 实例直调锚点（test_telegram_service.py:416/452）——自由函数形态会破坏 AC-2 测试零修改门；mixin 同时避免模块 import coordinator 的循环依赖。**Opus O1 判定更优，接受。**
2. **簇 M `_ensure_default_main_agent_bootstrap` 留 coordinator**（计划授权"若耦合面大则留"）：实测耦合 `_stores.project_store`×2 + `agent_context_store`×7 + `conn.commit()` 事务语义，移出无字节保真收益。**Opus O2 确认决策正确。**
3. **D11 残留扫描首轮漏根目录 `tests/`**：`test_f023_m2_acceptance.py` 4 处 `LLMWorkerAdapter` 引用（函数内 lazy import，collect 不报错）→ **wave 回归门抓住**（2 failed），Codex HIGH 同步命中。修复 = 4 处机械改名（import 行 + 实例化点，断言逻辑零改动）。**AC-2 例外记账**：rename 固有的引用更新，非测试迁就。教训：残留扫描必须覆盖 pyproject testpaths 全部 9 路径（含根 `tests/`），已纠正。
4. **Codex LOW-2**：`docs/codebase-architecture/modules/02-gateway-runtime-and-control-plane.md:432` `_build_registry()` 小节漂移 → 已同步为 `action_registry.build_action_registry()`（Blueprint 同步规则，W2 内闭环不留 W6）。

## 双评审 finding 闭环表

| Finding | severity | 处置 |
|---------|----------|------|
| Codex HIGH：f023 4 处 LLMWorkerAdapter import | HIGH | 已修（回归门先行抓住）；f023 重跑 5 passed |
| Codex LOW-1：provenance 旧名字符串 | LOW | 豁免 #4（仓库惯例，Opus O3 同向确认） |
| Codex LOW-2：02-gateway 模块文档漂移 | LOW | 已修（偏离 #4） |
| Opus O1：C2 mixin 形态/文件名偏离 | LOW(文档) | 接受 + 本清单记账 + refactor-plan 同步 |
| Opus O2：簇 M 留 coordinator | INFO | 决策正确确认 |
| Opus O3：溯源注记惯例 | INFO | 合规确认 |

**0 HIGH 残留。** Codex INFO 确认：import 子集完整 / module-level 求值时序等价（顶层仅 docstring+imports+def）/ MRO 无外部 type 断言影响（全仓 isinstance/type 断言 grep 空）/ 92 ActionDefinition 构建顺序 SHA256 一致。

## 验证

- 焦点：test_control_plane_api + test_telegram_service + test_orchestrator = 106 passed 0 failed（Opus 独立复跑）；f023 修复后 5 passed
- wave 回归门 v1：2 failed（f023，D11 漏改——门正确拦截）→ 修复后 **v2 全量重跑**（结果见 commit message）
- lint 基线：HEAD 12 findings → 改后 11（0 新增；1 处 E501 因去缩进顺带消失）
- e2e_smoke：commit hook 自动
