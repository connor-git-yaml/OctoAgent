# F108 计划双评审 — Codex adversarial 席位

> 评审对象：impact-report.md + refactor-plan.md + recon ×4。基线 d6148903。
> 整体判定：**需修订后执行**（方向可行：D9 三层收口 + F118/D11/D12 整合路径合理；4 HIGH + 4 MED 修订后进实现）。
> 主 session 验证状态：F1/F3/F4 已逐条 grep 实证确认（见文末）。

## Finding 清单

**F1 [HIGH]** `refactor-plan.md` W7 / `automation_service.py:288-291`
W7 只覆盖 `_get_service` 9 个调用点，但 `service_registry` 还被当 dict 直接遍历 `.values()`——`automation.create` 的 action_id 存在性校验汇总所有 domain service 的 `action_routes()`。typed registry 必须保留 `values()/all_services()` 等价物，并加 `automation.create` 有效/无效 action 回归。
**主 session 验证：确认**（automation_service.py:290 `for svc in self._ctx.service_registry.values()`）。

**F2 [HIGH]** `refactor-plan.md` W1 C2 / `worker_service.py:583-585` / `misc_tools.py:267-270`
W1 把 behavior 写入收口描述为"原子写"，但两个生产写入口都是 `write_text()` 直接覆盖。改 tmp+replace 会改变失败/权限/并发观察语义，非零变更。helper 默认保持 direct `write_text()`。
**与 Opus O1 附带纠正一致，接受。**

**F3 [HIGH]** `impact-report.md` / `test_hermetic_isolation.py`
"harness 11 段被逐段直调"不成立——实际直调/inspect 的 `_bootstrap_*` 是 **6 个符号**（`_bootstrap_paths`/`_bootstrap_stores`/`_bootstrap_tool_registry_and_snapshot`/`_bootstrap_owner_profile`/`_bootstrap_runtime_services` 直调 + `_bootstrap_executors` 经 `inspect.getsource`:254）。recon-A 引用的 `test_e2e_basic_tool_context.py:69/272` 与 `test_e2e_routine.py:54/76` 实为注释/断言消息，非直调。
**主 session 验证：确认**（grep 重建清单）。**决策不变**：harness 结构仍不动（main.py 唯一 caller + 纯 wiring 拆了 ROI 低），但事实表述修正。

**F4 [HIGH]** `refactor-plan.md` W5 / `test_capability_pack_web_search.py:35,40,45,51,57` / `test_phase_c_worker_to_worker.py:37`
capability_pack 测试直调面被低估：`CapabilityPackService._is_ddg_anomaly_page` / `._parse_duckduckgo_results` 被**类级直调**（staticmethod 经类访问）；`test_phase_c_worker_to_worker.py:37` 对 `_launch_child_task` 做 `inspect.getsource` 源码断言。mixin 化必须保证：方法仍在 `CapabilityPackService` MRO 上可达、descriptor 类型不变（staticmethod 保持 staticmethod）、`inspect.getsource` 可读。
**主 session 验证：确认。**

**F5 [MEDIUM]** `test_control_plane_api.py:1990-1993, 2125-2137, 2275-2285`
测试直接 monkeypatch/读取 `control_plane_service._mcp_service` 和 `._proxy_manager` 实例私有属性。W7 typed setter/字段封装不得改名或隐藏这些属性。
**接受**：W7 红线补充 instance 私有属性兼容。

**F6 [MEDIUM]** `behavior_workspace.py:17, 41-100, 1495-1499`
behavior_workspace 拆 package 的零变更依据需显式锁定 import 顺序、module-level 常量初始化、`@cache` 对象归属（单一定义模块 + 全调用方共享同一缓存实例）。
**接受**（与 Opus O6 验证互补——O6 证无双实例风险，F6 要求把它写进对账清单）。

**F7 [MEDIUM]** W8 / F108b 范围
F125/F124 LOW 修复和 AmbientRuntime prompt 布局行为变更混入 F108b 会让零变更 completion-report 验证矩阵失效。建议 W8 拆独立 feature/PR，或至少把 AmbientRuntime 从 F108 零变更验证矩阵显式移除。
**与 Opus O7 存在分歧**（Opus 认为独立 commit 即可）→ **列入人裁**。

**F8 [MEDIUM]** `worker_service.py:573-589` / `misc_tools.py:234-279`
`write_behavior_file_content()` 未定义错误契约。两调用方失败形态不同（ControlPlaneActionError vs BehaviorWriteFileResult + REVIEW_REQUIRED proposal 分支）。helper 只做低层 path/budget/write 返回可翻译结果，两端保留各自错误码 payload；加 golden response 对账测试。
**接受**（与 Opus O1 收敛同一方案）。

## 主 session 验证记录（2026-06-12）

```
F1: grep service_registry → automation_service.py:290 .values() 遍历确认
F3: grep "\._bootstrap_" tests → 真实直调 5 符号 + getsource 1 符号确认；
    test_e2e_basic_tool_context.py / test_e2e_routine.py 引用为注释/断言消息
F4: grep test_capability_pack_web_search.py → _is_ddg_anomaly_page(35/40/45)
    _parse_duckduckgo_results(51/57) 类级直调确认；
    test_phase_c_worker_to_worker.py:37 inspect.getsource 确认
```
