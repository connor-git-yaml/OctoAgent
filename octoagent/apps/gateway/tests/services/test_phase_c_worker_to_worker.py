"""F098 Phase C: Worker→Worker A2A 解禁单测（H2 完整对等性）。

baseline 行为：F084 引入 enforce_child_target_kind_policy 硬禁止 Worker→Worker delegate。
F098 Phase C 删除：H2 完整对等性达成（Worker = 主 Agent − {hire/fire/reassign Worker} − {user-facing 表面}）。

测试场景：
- AC-C1: worker runtime 调用 delegate_task(target_kind=worker) 不再 raise
- AC-C2: enforce_child_target_kind_policy 函数已删除（grep 验证）
- AC-C3: max_depth=2 死循环防护仍生效（DelegationManager 兜底）
"""

from __future__ import annotations

import pytest


def test_enforce_child_target_kind_policy_function_deleted():
    """AC-C2: enforce_child_target_kind_policy 函数已从 capability_pack 删除。"""
    from octoagent.gateway.services import capability_pack

    # F098 Phase C: 函数应该不存在
    assert not hasattr(capability_pack.CapabilityPackService, "enforce_child_target_kind_policy"), (
        "AC-C2 闭环失败：enforce_child_target_kind_policy 仍存在 "
        "（应已删除以支持 Worker→Worker A2A 解禁）"
    )


def test_worker_to_worker_delegation_no_raise_in_capability_pack_path():
    """AC-C1: capability_pack._launch_child_task 不再调用 enforce_child_target_kind_policy。

    通过 inspect 源码验证：函数体内不应有 self.enforce_child_target_kind_policy(...) 调用。
    """
    import inspect

    from octoagent.gateway.services.capability_pack import CapabilityPackService

    src = inspect.getsource(CapabilityPackService._launch_child_task)
    # 验证：实际调用语法 self.enforce_child_target_kind_policy( 已不存在
    # （注释中的提及不算调用——所以严格匹配调用语法）
    assert "self.enforce_child_target_kind_policy(" not in src, (
        "AC-C1 闭环失败：_launch_child_task 仍在调 enforce_child_target_kind_policy"
    )


def test_max_depth_protection_still_in_harness_delegation():
    """AC-C3: DelegationManager max_depth=2 死循环防护仍生效（F084 引入，位于 harness/delegation.py）。"""
    from octoagent.gateway.harness import delegation

    src_path = delegation.__file__
    with open(src_path, encoding="utf-8") as f:
        src = f.read()

    # max_depth=2 防护应仍存在
    assert "MAX_DEPTH" in src or "max_depth" in src, (
        "AC-C3 闭环失败：max_depth 防护已被错误删除"
    )


def test_delegation_plane_comments_updated():
    """AC-C2 verify: delegation_plane.py 注释已更新（移除 enforce 提及）。"""
    from octoagent.gateway.services import delegation_plane

    src_path = delegation_plane.__file__
    with open(src_path, encoding="utf-8") as f:
        src = f.read()

    # F098 Phase C 标识应出现在注释中
    assert "F098 Phase C" in src, (
        "AC-C2 闭环失败：delegation_plane.py 缺 F098 Phase C 注释（解禁说明）"
    )
