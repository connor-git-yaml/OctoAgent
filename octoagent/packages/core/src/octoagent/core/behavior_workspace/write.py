"""Behavior 文件写入两段式收口（F108a W1 C2 / D12）。

`worker_service._handle_behavior_write_file`（control_plane action 入口）与
`misc_tools.behavior_write_file`（builtin tool 入口）此前各自重复
"路径解析 → 预算检查 → mkdir → write_text" 写序列（D12 债）。本模块把该
**写核**收口为 prepare / commit 两段：

- ``prepare_behavior_file_write``：路径解析 + 预算检查，不触盘；
- ``commit_behavior_file_write``：mkdir + 非原子 ``write_text``。

两段之间留给调用方放置各自的门（misc_tools 的 REVIEW_REQUIRED proposal 门
正卡在预算检查与写入之间）。预算超限的拒绝形态、错误包装、事件发射、
onboarding marker、behavior pack cache 失效均为调用方差异化副作用，
**刻意不在本模块**（F108 双评审 O1/F2/F8 收敛边界）。
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from .budget import BehaviorBudgetResult, check_behavior_file_budget
from .paths import resolve_write_path_by_file_id


@dataclass(frozen=True, slots=True)
class PendingBehaviorWrite:
    """prepare 阶段产物：已解析路径 + 预算结果，尚未触盘。"""

    resolved: Path
    budget: BehaviorBudgetResult


def prepare_behavior_file_write(
    project_root: Path,
    file_id: str,
    content: str,
    *,
    agent_slug: str,
    project_slug: str,
) -> PendingBehaviorWrite:
    """写核第一段：解析写入路径 + 预算检查，不写盘。

    Raises:
        ValueError: file_id 非法（``resolve_write_path_by_file_id`` 原样上抛，
            调用方按各自错误契约包装）。
    """
    resolved = resolve_write_path_by_file_id(
        project_root,
        file_id,
        agent_slug=agent_slug,
        project_slug=project_slug,
    )
    return PendingBehaviorWrite(
        resolved=resolved,
        budget=check_behavior_file_budget(file_id, content),
    )


def commit_behavior_file_write(pending: PendingBehaviorWrite, content: str) -> None:
    """写核第二段：mkdir(parents, exist_ok) + 非原子 utf-8 ``write_text``。

    与收口前两入口的写序列字节级同序；写入异常原样上抛，由调用方按各自
    错误契约包装（control_plane 抛 ControlPlaneActionError，builtin tool
    返回 rejected BehaviorWriteFileResult）。
    """
    pending.resolved.parent.mkdir(parents=True, exist_ok=True)
    pending.resolved.write_text(content, encoding="utf-8")
