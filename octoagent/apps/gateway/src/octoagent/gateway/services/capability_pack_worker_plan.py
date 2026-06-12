"""F108a W5：CapabilityPackService 的 worker plan 职责簇 mixin。

职责边界：worker review / apply 提案流——objective 拆分、assignment 构建、
merge / repartition / split 提案。新增 worker 计划类方法放这里，防止职责
堆回 capability_pack.py。

模块级 ``_WorkerPlanAssignment`` / ``_WorkerPlanProposal`` /
``_WORK_TERMINAL_VALUES`` 一并落本模块（仅本簇方法使用，单一定义；
W4 范式：模块常量随簇迁移）。

``_launch_child_task`` **刻意留主文件**（不进本 mixin）：
test_capability_pack_phase_d.py 经字符串路径
``patch("octoagent.gateway.services.capability_pack.get_current_execution_context")``
patch 主模块命名空间——函数名字解析走定义模块 ``__globals__``，方法迁出后
该 patch 永不生效（W5 实测 7 用例失败）。测试零修改红线下唯一干净解是
方法留在 patch 语义绑定的模块；``apply_worker_plan`` 经
``self._launch_child_task`` MRO 调用，行为不变。

依赖约定（由继承类 CapabilityPackService 提供，经 MRO 解析）：
- ``self._delegation_plane`` / ``self._task_runner``（bind_* 注入）
- ``self._stores``（主类 ``__init__``）
- ``self._launch_child_task``（主文件，理由见上）
"""

from __future__ import annotations

from typing import Any

from octoagent.core.models import (
    WORK_TERMINAL_STATUSES,
    RuntimeKind,
)
from pydantic import BaseModel, Field
from ulid import ULID

class _WorkerPlanAssignment(BaseModel):
    objective: str = Field(min_length=1)
    worker_type: str = Field(default="research")
    target_kind: str = Field(default="subagent")
    tool_profile: str = Field(default="minimal")
    title: str = Field(default="")
    reason: str = Field(default="")


class _WorkerPlanProposal(BaseModel):
    plan_id: str = Field(min_length=1)
    work_id: str = Field(default="")
    task_id: str = Field(default="")
    proposal_kind: str = Field(default="split")
    objective: str = Field(default="")
    summary: str = Field(default="")
    requires_user_confirmation: bool = True
    assignments: list[_WorkerPlanAssignment] = Field(default_factory=list)
    merge_candidate_ids: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)


_WORK_TERMINAL_VALUES = {s.value for s in WORK_TERMINAL_STATUSES}


class WorkerPlanMixin:
    """Worker plan 职责簇：见模块 docstring。

    本 mixin 不持有状态，所有依赖（self._delegation_plane / self._task_runner /
    self._stores / self._launch_child_task——后者刻意留主文件，理由见模块
    docstring）由继承类 CapabilityPackService 提供。方法签名、返回值与
    副作用与拆分前完全等价（F108a 行为零变更）。
    """

    async def review_worker_plan(
        self,
        *,
        work_id: str,
        objective: str = "",
    ) -> _WorkerPlanProposal:
        if self._delegation_plane is None:
            raise RuntimeError("delegation plane is not bound for worker review")
        work = await self._stores.work_store.get_work(work_id)
        if work is None:
            raise RuntimeError(f"work not found: {work_id}")
        task = await self._stores.task_store.get_task(work.task_id)
        if task is None:
            raise RuntimeError(f"task not found for work: {work.task_id}")
        descendants = await self._delegation_plane.list_descendant_works(work_id)
        proposal_objective = objective.strip() or work.title or task.title
        fragments = self._split_worker_objectives(proposal_objective)
        if not fragments:
            fragments = [proposal_objective or "review current work and propose next action"]

        active_descendants = [
            item for item in descendants if item.status.value not in _WORK_TERMINAL_VALUES
        ]
        terminal_descendants = [
            item for item in descendants if item.status.value in _WORK_TERMINAL_VALUES
        ]
        if (
            descendants
            and not objective.strip()
            and terminal_descendants
            and not active_descendants
        ):
            proposal_kind = "merge"
        elif descendants and objective.strip():
            proposal_kind = "repartition"
        else:
            proposal_kind = "split"

        assignments = (
            [
                self._build_worker_assignment(item, index=index)
                for index, item in enumerate(fragments, 1)
            ]
            if proposal_kind in {"split", "repartition"}
            else []
        )
        warnings: list[str] = []
        if proposal_kind == "merge" and active_descendants:
            warnings.append("仍有 child works 在运行，当前不能直接 merge。")
        if proposal_kind == "repartition" and active_descendants:
            warnings.append("apply 时会先取消当前仍在运行的 child works，再按新计划重划分。")
        if not descendants and proposal_kind == "merge":
            warnings.append("当前 work 还没有 child works，merge 不会生效。")

        summary = {
            "merge": "建议合并已完成的 child works，并回收当前父 work。",
            "repartition": "建议先收拢现有 child works，再按新计划重新划分 worker。",
            "split": "建议按可执行子任务拆分给具体 worker，而不是让主 Agent 直接动手。",
        }[proposal_kind]
        return _WorkerPlanProposal(
            plan_id=str(ULID()),
            work_id=work.work_id,
            task_id=work.task_id,
            proposal_kind=proposal_kind,
            objective=proposal_objective,
            summary=summary,
            assignments=assignments,
            merge_candidate_ids=[item.work_id for item in terminal_descendants],
            warnings=warnings,
        )

    async def apply_worker_plan(
        self,
        *,
        plan: dict[str, Any] | _WorkerPlanProposal,
        actor: str = "control_plane",
    ) -> dict[str, Any]:
        if self._delegation_plane is None:
            raise RuntimeError("delegation plane is not bound for worker apply")
        if self._task_runner is None:
            raise RuntimeError("task runner is not bound for worker apply")
        proposal = (
            plan
            if isinstance(plan, _WorkerPlanProposal)
            else _WorkerPlanProposal.model_validate(plan)
        )
        work = await self._stores.work_store.get_work(proposal.work_id)
        if work is None:
            raise RuntimeError(f"work not found: {proposal.work_id}")
        task = await self._stores.task_store.get_task(work.task_id)
        if task is None:
            raise RuntimeError(f"task not found for work: {work.task_id}")

        descendants = await self._delegation_plane.list_descendant_works(work.work_id)
        cancelled_work_ids: list[str] = []
        if proposal.proposal_kind == "repartition":
            for child in descendants:
                if child.status.value in _WORK_TERMINAL_VALUES:
                    continue
                await self._task_runner.cancel_task(child.task_id)
                await self._delegation_plane.cancel_work(
                    child.work_id,
                    reason=f"worker_review_repartition:{actor}",
                )
                cancelled_work_ids.append(child.work_id)
        if proposal.proposal_kind == "merge":
            merged = await self._delegation_plane.merge_work(
                work.work_id,
                summary=f"worker review approved by {actor}",
            )
            return {
                "plan_id": proposal.plan_id,
                "proposal_kind": proposal.proposal_kind,
                "cancelled_work_ids": cancelled_work_ids,
                "child_tasks": [],
                "merged_work": None if merged is None else merged.model_dump(mode="json"),
            }

        child_tasks = [
            await self._launch_child_task(
                parent_task=task,
                parent_work=work,
                objective=item.objective,
                worker_type=item.worker_type,
                target_kind=item.target_kind,
                tool_profile=item.tool_profile,
                title=item.title,
                spawned_by="worker_review_apply",
                plan_id=proposal.plan_id,
            )
            for item in proposal.assignments
        ]
        return {
            "plan_id": proposal.plan_id,
            "proposal_kind": proposal.proposal_kind,
            "cancelled_work_ids": cancelled_work_ids,
            "child_tasks": child_tasks,
            "merged_work": None,
        }

    @staticmethod
    def _split_worker_objectives(objective: str) -> list[str]:
        normalized = objective.strip()
        if not normalized:
            return []
        for token in ["\r\n", "；", ";", "。", "，然后", "然后", "并且", "接着", "再"]:
            normalized = normalized.replace(token, "\n")
        items = [item.strip(" -\t") for item in normalized.splitlines() if item.strip(" -\t")]
        if len(items) > 1:
            return items[:4]
        return [normalized]

    @staticmethod
    def _effective_tool_profile_for_objective(*, objective: str) -> str:
        del objective
        return "standard"

    def _build_worker_assignment(
        self,
        objective: str,
        *,
        index: int,
    ) -> _WorkerPlanAssignment:
        tool_profile = "standard"
        return _WorkerPlanAssignment(
            objective=objective,
            worker_type="general",
            target_kind=RuntimeKind.SUBAGENT.value,
            tool_profile=tool_profile,
            title=f"worker-{index}",
            reason="子任务由 worker 处理。",
        )
