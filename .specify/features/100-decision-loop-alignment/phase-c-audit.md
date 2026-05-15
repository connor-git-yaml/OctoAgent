# F100 Phase C — Consumed 时点 Audit + 关键发现

**Date**: 2026-05-15
**Phase**: C（consumed audit + fixture 准备）
**Status**: Audit 完成 → 关键发现需用户拍板 v0.3 修订

---

## 1. Production consumed 时点清单

| # | 文件:行号 | helper | 类型 | runtime_context 来源 |
|---|-----------|--------|------|--------------------|
| 1 | `task_service.py:1134` | `is_recall_planner_skip(runtime_context, dispatch_metadata)` | **post-decision** | `_build_memory_recall_plan` 参数 → 上游 `_build_task_context` 解析（line 1214: `request.runtime_context or runtime_context_from_metadata(metadata)`）→ orchestrator dispatch 时已 patch |
| 2 | `llm_service.py:383` | `is_single_loop_main_active(runtime_context, metadata)` | **mixed**（取决于 metadata 是否含 runtime_context_json） | `runtime_context_from_metadata(metadata)` (line 382) —— 没有 fallback 到外部 runtime_context |
| 3 | `orchestrator.py:771` | `is_single_loop_main_active(runtime_context_for_check, metadata)` | **pre-decision**（决定 single_loop vs routing） | `request.runtime_context or runtime_context_from_metadata(metadata)` (line 770) —— **chat.py seed 是 unspecified** |
| 4 | `orchestrator.py:1050` | `is_single_loop_main_active(runtime_context_for_check, request.metadata)` | **pre-decision**（routing 决策） | 同 #3 |

**核心发现**：4 个 production consumed 时点中 **3 个是 pre-decision**（orchestrator 2 处 + llm_service 1 处可能为 None runtime_context），只有 1 个是 post-decision（task_service）。

---

## 2. chat.py seed context 派生路径

`chat.py:433-445` 和 `chat.py:482-493` 构造 `RuntimeControlContext`：
- **不传** `delegation_mode`（保持默认 "unspecified"）
- 写入 `dispatch_metadata[RUNTIME_CONTEXT_JSON_KEY]`（即 metadata["runtime_context_json"]）

派生路径：
```
chat.py: RuntimeControlContext(delegation_mode="unspecified")
  ↓ 写入 dispatch_metadata[RUNTIME_CONTEXT_JSON_KEY]
  ↓ enqueue / run → OrchestratorRequest.metadata
  ↓ orchestrator.py:770: runtime_context_for_check = request.runtime_context or runtime_context_from_metadata(metadata)
  ↓                       = decoded unspecified RuntimeControlContext（非 None）
  ↓ orchestrator.py:771: is_single_loop_main_active(unspecified_rc, metadata)
    F091 现状（baseline）：unspecified → fallback metadata_flag("single_loop_executor") → False
    F100 v0.2 "consumed 时 raise" 方案：unspecified → raise ValueError → 破坏 baseline
```

---

## 3. v0.2 "consumed 时 raise" 方案的破坏面

如果按 v0.2 spec/plan：
- `orchestrator._prepare_single_loop_request` 调用 `is_single_loop_main_active(unspecified_rc, ...)` → raise → orchestrator 整个 dispatch 链路崩
- 影响：**所有 chat.py 请求**（首条请求 + 后续请求都走 `_prepare_single_loop_request`）
- baseline 行为彻底破坏

---

## 4. 修订方向：v0.3 — unspecified → return False（不 raise）

### 4.1 方案核心

**两个 helper 的 unspecified 处理**：
- `is_single_loop_main_active(unspecified_rc | None, metadata)` → **return False**
  - 语义：pre-decision 默认走 standard routing（与 baseline 默认 metadata_flag 缺失时返回 False 等价）
- `is_recall_planner_skip(unspecified_rc | None, metadata)` → **return False**
  - 语义：post-decision 默认走 full recall（与 baseline 默认 metadata_flag 缺失时返回 False 等价）

### 4.2 与 v0.2 的差异

| 项 | v0.2 | v0.3 |
|----|------|------|
| unspecified consumed 行为 | raise ValueError | return False |
| baseline 兼容性 | 破坏 chat 主链 | 100% 兼容 |
| metadata fallback 移除 | 是 | 是 |
| 错误检测能力 | 强（fail-fast 漏 patch 路径）| 弱（漏 patch 静默走 False）|
| F107 升级路径 | 不变 | 不变 |

### 4.3 错误检测补强

v0.3 牺牲了 fail-fast，但可以通过其他手段补强错误检测：
- **测试覆盖**：单测验证关键 production 路径 patch 后 helper 调用得到预期值
- **日志告警**（可选）：unspecified 触达 helper 时记录 debug log（不 raise）
- **集成测试断言**：chat → orchestrator → task_service 全链路覆盖 patch 必经路径

### 4.4 AC-8 / AC-9 重写（v0.3）

**AC-8（修订）**：unspecified delegation_mode consumed 时 return False

```
is_recall_planner_skip(RuntimeControlContext(delegation_mode="unspecified"), metadata):
  - F100 前：fallback metadata flag（return True if metadata["single_loop_executor"]）
  - F100 后：return False（无 metadata fallback；与 baseline metadata 缺失时等价）

is_single_loop_main_active 同理。
```

**AC-9（修订）**：consumed 时点 delegation_mode 一致性测试

不要求 unspecified 触发 raise；改为验证：
- 关键 patch 必经路径（orchestrator._prepare_single_loop_request / orchestrator._with_delegation_mode）显式设置 delegation_mode 后 helper 调用得到预期值
- 测试 fixture 覆盖：unspecified 直接 consumed 时 return False（baseline 兼容）
- 测试 fixture 覆盖：显式 delegation_mode patch 后 helper 调用得到预期值

---

## 5. RuntimeControlContext 构造点分类（仅记录，不动）

| 类型 | 构造点 | delegation_mode |
|------|--------|-----------------|
| pre-decision seed | `chat.py:433-445`, `chat.py:482-493` | unspecified（default） |
| post-decision patched | `orchestrator._with_delegation_mode` 派生 | 显式（main_inline / main_delegate / 等） |
| dispatch internal | `dispatch_service._resolve_a2a_source_role` 派生 | 显式（F098/F099 已就位） |
| worker runtime | `worker_runtime.py` 路径 | 显式（依 caller kind） |
| test fixture | `tests/test_runtime_control_f091.py` 等 | 各种值（含 unspecified） |

**结论**：所有 production 构造点已按 baseline 正确分类。pre-decision seed 必须保留 unspecified；post-decision patched 必须显式（已 baseline 行为）。F100 不动构造点。

---

## 6. Phase C 测试 fixture 骨架（preview）

```python
# tests/test_runtime_control_f100.py（Phase D 实施时填充）
import pytest
from octoagent.core.models import RuntimeControlContext

@pytest.fixture
def unspecified_rc():
    """pre-decision seed context（chat.py 风格）"""
    return RuntimeControlContext(task_id="test-task-1")  # delegation_mode default unspecified

@pytest.fixture
def main_inline_rc():
    return RuntimeControlContext(
        task_id="test-task-1",
        delegation_mode="main_inline",
        recall_planner_mode="skip",
    )

@pytest.fixture
def worker_inline_rc():
    return RuntimeControlContext(
        task_id="test-task-1",
        delegation_mode="worker_inline",
        recall_planner_mode="skip",
    )

@pytest.fixture
def main_delegate_rc():
    return RuntimeControlContext(
        task_id="test-task-1",
        delegation_mode="main_delegate",
        recall_planner_mode="full",
    )

@pytest.fixture
def subagent_rc():
    return RuntimeControlContext(
        task_id="test-task-1",
        delegation_mode="subagent",
        recall_planner_mode="full",
    )

# Phase D 后用：
# @pytest.fixture
# def force_full_recall_rc():
#     return RuntimeControlContext(
#         task_id="test-task-1",
#         delegation_mode="main_inline",
#         recall_planner_mode="auto",
#         force_full_recall=True,
#     )
```

---

## 7. Phase C 总结

- ✅ 4 个 production consumed 时点 audit 完成
- ✅ chat.py seed context 派生路径追踪完成
- ❗ **重大发现**：3/4 consumed 时点是 pre-decision，v0.2 "consumed raise" 方案破坏 baseline
- ✅ v0.3 修订方向：unspecified → return False（与 baseline 兼容）
- ✅ RuntimeControlContext 构造点分类完成
- ✅ Phase D 测试 fixture 骨架准备完成
- ⏸️ **等待用户拍板 v0.3 修订**

---

**Next Action**：用户拍板 v0.3 修订方向后更新 spec/plan，然后 commit Phase C。
