# OctoAgent Module 单例 Reset 清单（F087 P2 T-P2-1 实证产物）

**生成时间**：2026-04-30
**任务**：F087 P2 T-P2-1（R3 关键缓解）
**实证方式**：plan §3 Risk #3 全 5 条 grep 命令实跑
**目的**：列出 e2e_live conftest 内 `_reset_module_state` autouse fixture 必须清理的全部 module
级 stateful 单例。每条记录 module path / 变量名 / reset 方式 / import-time default。

---

## 实证 grep 命令清单（已全跑）

```bash
# 1. module-level 单例（dict / set / list / 简单 = 赋值）
grep -rn "^_[a-z_][a-zA-Z0-9_]* *=" \
  apps/gateway/src/octoagent/gateway/harness/ \
  apps/gateway/src/octoagent/gateway/services/

# 2. ContextVar 全部
grep -rEn "ContextVar\b" packages/ apps/gateway/src

# 3. classmethod 改类属性 pattern
grep -rn "cls\._[a-z_][a-zA-Z0-9_]* *=" \
  apps/gateway/src/octoagent/gateway/services/

# 4. lru_cache / cache decorator
grep -rn "@\(functools\.\)\?lru_cache\|@\(functools\.\)\?cache" \
  apps/gateway/src/octoagent/gateway/

# 5. 模块级 list / dict / set 字面量赋值
grep -rn "^[A-Z_][A-Z0-9_]* *= *\(\[\]\|{}\|set()\)" \
  apps/gateway/src/octoagent/gateway/harness/ \
  apps/gateway/src/octoagent/gateway/services/
```

实证结果：grep 4 / grep 5 在 harness/ 与 services/ 下命中为空。grep 3 仅命中
`AgentContextService` 两条。grep 1 / grep 2 命中如下，过滤掉无状态项（`_log =
structlog.get_logger()`、`_PI_xxx` 等不可变 ThreatPattern 常量、字符串/正则/类型
常量）后得到的"实际 stateful 单例"清单见下表。

---

## 必清单例清单（按 reset 优先级 / 影响域排序）

| # | Module 路径 | 变量名 | 类型 | Reset 方式 | Import-time Default | 来源 grep |
|---|------------|--------|------|-----------|--------------------|----------|
| 1 | `apps/gateway/src/octoagent/gateway/harness/tool_registry.py` | `_REGISTRY` | `ToolRegistry` 实例（含 `_entries: dict[str, ToolEntry]`） | `module._REGISTRY._entries.clear()`（保持单例 identity，仅清条目） | `ToolRegistry()`（空 dict） | grep 1 |
| 2 | `apps/gateway/src/octoagent/gateway/services/agent_context.py` | `AgentContextService._shared_llm_service` | classmethod 类属性 `Any \| None` | `AgentContextService._shared_llm_service = None` | `None` | grep 3 |
| 3 | `apps/gateway/src/octoagent/gateway/services/agent_context.py` | `AgentContextService._shared_provider_router` | classmethod 类属性 `Any \| None` | `AgentContextService._shared_provider_router = None` | `None` | grep 3 |
| 4 | `apps/gateway/src/octoagent/gateway/services/execution_context.py` | `_CURRENT_EXECUTION_CONTEXT` | `ContextVar[ExecutionRuntimeContext \| None]` | `_CURRENT_EXECUTION_CONTEXT.set(None)` | `None`（构造期 default） | grep 2 |
| 5 | `apps/gateway/src/octoagent/gateway/services/context_compaction.py` | `_tiktoken_encoder` | `Any \| None`（lazy 初始化的 encoder 对象） | `module._tiktoken_encoder = None` | `None` | grep 1 |

---

## 主动审视：harness 段实例 stateful 容器（实例级 / 非 module 级）

下列单例属于 **harness 实例**而非 module 级，由 `OctoHarness.shutdown()` 释放
即可，**不需要在 `_reset_module_state` 内单独清理**——只需保证 e2e fixture 每
轮重新构造 OctoHarness 即可隔离：

| Module | 实例字段 | 释放路径 |
|--------|---------|---------|
| `harness/approval_gate.py` | `ApprovalGate._audit_task_ensured: set[str]` | OctoHarness 重建后实例随之新建 |
| `harness/delegation.py` | `DelegationManager._audit_task_ensured: set[str]` | OctoHarness 重建后实例随之新建 |
| `harness/snapshot_store.py` | `SnapshotStore._snapshot` | OctoHarness 重建后实例随之新建 |
| `harness/threat_scanner.py` | `_PI_xxx` / `_RH_xxx` / `_CLEAN_RESULT` 等 | **不可变常量，无需 reset** |

---

## 审视：grep 1 命中但**不需要 reset** 的项（无状态 / 不可变）

下列项在 grep 1 命中，但不属于"stateful 单例"，**不进 reset 清单**：

- 全部 `_log = structlog.get_logger()`（无状态 logger 工厂）
- `harness/approval_gate.py:_APPROVAL_AUDIT_TASK_ID = "_approval_gate_audit"`（字符串常量）
- `harness/delegation.py:_DELEGATION_AUDIT_TASK_ID = "_delegation_manager_audit"`（字符串常量）
- `harness/threat_scanner.py:_PI_xxx` / `_RH_xxx` / `_EX_xxx` / `_B64_xxx` /
  `_SO_xxx` / `_MI_xxx` / `_CLEAN_RESULT` / `_INVISIBLE_CHARS` / `_MEMORY_THREAT_PATTERNS`
  （ThreatPattern / frozenset / dataclass，全部 immutable / 仅读）
- `harness/snapshot_store.py:RESULT_SUMMARY_MAX_LEN = 500`（int 常量）
- `services/memory/builtin_memu_bridge.py:_QWEN_MODEL_ID` / `_QWEN_LAYER_ID` / 等（字符串常量）
- `packages/core/.../sqlite_init.py:_TASKS_DDL` 等 DDL 字符串（不可变）
- `packages/core/.../checkpoint_store.py:_CHECKPOINT_TRANSITIONS` （类型 dict 但内容为不可变 set，运行期不被改写）

---

## 失效信号（P3 5x 循环验证时必查）

- 任一场景"alone pass / together fail" → 漏一项 reset，二分定位
- 5x 循环出现"同一测重跑结果不一致" → 实例级状态泄漏（OctoHarness 没真重建）
- `_REGISTRY` 工具数跑前 ≠ 跑后 → register 没清理或多注册了

---

## 维护守则

1. **新加生产代码引入 module 级 stateful 单例时**，必须同步更新本文件 +
   `_reset_module_state` fixture
2. **本文件由 `_reset_module_state` fixture 引用**，fixture 自身的单测
   （T-P2-7 内 sanity test）会断言每条 reset 有效
3. P3 上线后任何"alone pass / together fail"必须先回查本文件**是否漏项**
