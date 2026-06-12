# F108b W7 对账清单（F118 D8 typed DI）

> 双评审：Codex **CONDITIONAL PASS（1 LOW 已闭环）** + Opus **APPROVE 0H/0M/2L（已闭环）**。

## 改动

- `ControlPlaneServiceRegistry`（_base.py）：9 个 concrete 字段（TYPE_CHECKING import 防环）+ `all_services()` 原 dict 插入序；**缺字段构造期 TypeError**——运行期字符串查找断链前移到构造期（F118 目标本身，Opus O4 判"等价且更严"）。
- `ControlPlaneContext.service_registry: dict[str,Any]` → `services: Registry | None`；`_get_service` 删除 → `_require_services` + 3 个 typed property（`_agent_domain`/`_mcp_domain`/`_setup_domain`，concrete 返回类型——O3 红线：3 处跨 service 私有方法调用实测 hasattr 不断链）。
- **错误语义字节级等价**：未注入时 RuntimeError + `service '<name>' 未在 service_registry 中注册`（新测试 parametrize ×3 锁 utf-8 逐字节，Opus 独立 encode 比对 True）。
- 9 个调用点替换（setup 8 + mcp 1）；automation `.values()` → `all_services() if not None else ()`（保"空注册表→空集合"原语义，Codex #2 确认无第三态）。
- coordinator bind_* 3 方法改调子 service setter（automation 用既有 `bind_automation_scheduler`；mcp/setup 新增）；**coordinator 自身实例属性赋值原样保留**（F5 红线：test_control_plane_api:1990/2125/2275 直读）。

## 豁免/偏离

| # | 项 | 处置 |
|---|---|------|
| 1 | **plan §W7"fail-fast accessor"未实现，仅 typed setter**（Opus O1） | **偏离正确且必需**：`_proxy_manager`/`_mcp_installer` 全部消费方是 Constitution #6 故意降级路径（setup:1029/1186 None 静默跳过；mcp:112 None→空记录；mcp:206/256/285 None→专属 `MCP_INSTALLER_UNAVAILABLE` ActionError）——加 fail-fast 会把降级/专属错误码改成通用异常 = 行为变更。plan 字面与 impact-report §三"显式 typed setter"内部不一致，实施跟随后者（保语义优先）。**C1 跨 service registry（fail-fast 构造期）与 C2 外部资源（graceful-degrade）二分处理是本 wave 核心判断** |
| 2 | per-key 缺失态在新设计结构性不可达（Codex #1 / Opus O4）| 全仓 grep 无"塞非 9 键"序列；缺失态唯余 None（字节等价 RuntimeError 覆盖）。新错误覆盖面 ⊇ 旧 |
| 3 | TYPE_CHECKING 环风险（Codex #3）| 运行期零环；全仓无 get_type_hints/model_rebuild 触发 forward-ref 解析 |
| 4 | automation 退化分支 `else ()` 仅 bare-ctx 可达（Opus O5）| 与原空 dict 迭代行为等价，生产不可达 |

## 双评审 finding 闭环表

| Finding | 处置 |
|---------|------|
| Codex F1（LOW）：all_services 迭代缺 killing test | **已补**：`test_automation_create_accepts_domain_action_via_all_services`——domain action（behavior.read_file）→ AUTOMATION_CREATED + 未知 action → AUTOMATION_ACTION_INVALID（首版断言猜错真实 code 被测试自身纠正两轮，最终锁最强形态） |
| Opus O2（LOW）：w7-ledger 缺失 | 本文件 |
| Opus O3（LOW）：setup setter 空行 PEP8 | 已修（setter 移 __init__ 后、section banner 前） |
| Opus O1 / O4 / O5（INFO）| 豁免表 #1/#2/#4 |

## 验证

- 全量门：**4134 passed / 0 failed**（= 4130 + 4 新 registry 测试；killing test 补强后焦点复跑 96 passed）/ 5:42
- 焦点：registry 5 + control_plane_api + telegram = 96 passed；Opus 加跑 services/ 全目录 + scheduler 280 passed
- 残留扫描：`_get_service`/`service_registry` 全仓（含根 tests/ + docs）零 live-code 残留（仅错误串字面量 + docstring 溯源）
- e2e_smoke：commit hook 自动

**0 HIGH 残留。**
