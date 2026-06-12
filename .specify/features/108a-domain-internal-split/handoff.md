# F108a → F108b Handoff

> F108a（W1-W5 域内机械拆分）完成待合入。F108b（W6-W8 跨层契约收口）**在 F108a 合入 master 后**从干净基线启动。

## F108b 范围（program plan v2 §W6-W8，用户 2026-06-12 已拍板）

- **W6**：`_ApprovalOverrideMemoryCache`（capability_pack.py:~107-155，W5 后行号）下沉 packages/tooling + living-docs 漂移闸（harness-and-context.md / module-design.md 三层职责定调：broker=执行运行时+registry SoT / cap_pack=治理面+pack 投影 / harness=纯 wiring；截断/错误包装/双 safety-scan 现状文档化；Manus 稳定排序/az-1 扩展缝记设计原则）。
- **W7 F118 typed DI**：typed registry（9 concrete service 类字段 + `all_services()` 迭代等价物——automation_service.py:290 `.values()` 用法）；`_get_service` 9 调用点 → typed accessor（**错误语义字节级等价**：裸 RuntimeError 同 message，先补错误路径单测）；7 处跨 service 属性赋值 → typed setter；bind_* 3 方法保留（ControlPlane↔AutomationScheduler 真循环依赖）。红线：`monkeypatch.setattr(control_plane_module, ...)` 模式 + `control_plane_service._mcp_service`/`._proxy_manager` 实例属性兼容（test_control_plane_api.py:1990/2125/2275）+ fail-fast 时机前移不改 happy-path 验证。
- **W8 顺手项**：F124/F125 遗留 LOW（`assert len>=15` -O 安全化 / scan_context docstring / render 幂等评估 / **research handoff 现场 new service**——`agent_context_prompt_assembly.py:446`，动前读 test_tool_result_threat_scan no-bypass 断言）+ **AmbientRuntime 秒级时间戳挪出冻结前缀**（用户拍板：折入 F108b **独立 commit**、显式行为变更标注、排除出零变更验证矩阵、completion-report 单列、可单独 bisect/revert）。

## F108a 沉淀的红线库（F108b 必读）

1. **模块级 patch 命名空间耦合**（W5 新发现）：迁移任何符号前 grep 全部测试 `patch(`/`monkeypatch.setattr(` 字符串含宿主模块名；被 patch 符号必须留宿主命名空间（或留 import 锚点 + noqa 注释）。已知锚点：capability_pack 的 `httpx` + `get_current_execution_context` + `_ssrf_request_hook` re-export；control_plane `__init__.py:7` 模块级 re-export。
2. 残留扫描覆盖 pyproject testpaths 全部 9 路径（含根 `tests/`）。
3. AST Name-Load 断言替代 grep（字符串字面量陷阱）。
4. 对账工具：`108a-domain-internal-split/tools/check_move_fidelity.py`（顶层符号）+ `check_method_move.py`（类方法）——F108b W6 cache 类下沉直接复用。
5. setup_service `_cp_pkg` 间接引用（48/1328，W3 后行号）monkeypatch 依赖不可改直接 import。

## 基线与账本

- F108a 分支最终态：13 个 commit（计划制品 1 + W1×3 + W2×3 + W3×2 + W4×2 + W5×2 + cleanup 1 + 收口 docs 1[待提交]）。
- 回归账本：baseline 4091 passed（d6148903）+ F108a 新增 14 golden = 4105 预期；6 个 e2e_live 真实 LLM 测试环境性挂起按名单记账（provider 侧问题，诊断 chip 已派）；test_sc3_projection 偶发 F083 race（隔离复跑稳过）。
- e2e_smoke 8/8（hook 每 commit 验证）。

## 风险与提醒

1. **Claude 订阅月度限额**：W5 Opus 评审席中断。F108b 每 wave 双评审若限额未恢复，需用户拍板：等恢复 / 第二席换 sonnet / 主 session 接管（F103c 先例，独立性弱一档）。
2. F108b W7 与 W6 都触碰 capability_pack/coordinator——严格串行。
3. capability_pack 主文件可再压 ~130 行（_launch_child_task），但需改 phase_d 测试 patch 路径 = 测试修改，建议 F108b W8 顺手评估或显式放弃。
4. CLAUDE.local.md 的 M6 表 F108 行需在合入时更新（F108a/F108b 拆分 + F108a 状态）。
