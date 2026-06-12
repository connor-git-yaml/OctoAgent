"""Telegram 命令解析 mixin（F108a W2-C2 自 _coordinator 抽出）。

ControlPlaneService 通过继承本 mixin 获得 build_telegram_action_request；
方法体内的 self.get_action_definition 由宿主类经 MRO 在运行时解析，
本模块不 import coordinator，避免循环依赖。
"""

from __future__ import annotations

from typing import Any

from octoagent.core.models import (
    ActionRequestEnvelope,
    ControlPlaneActor,
    ControlPlaneSurface,
)
from ulid import ULID


class TelegramCommandMixin:
    """Telegram 命令 → ActionRequestEnvelope 解析（宿主类需提供 get_action_definition）。"""

    def build_telegram_action_request(
        self,
        text: str,
        *,
        actor_id: str,
        actor_label: str,
    ) -> ActionRequestEnvelope | None:
        raw = text.strip()
        if not raw.startswith("/"):
            return None
        parts = raw.split()
        if not parts:
            return None

        action_id = ""
        params: dict[str, Any] = {}
        command = parts[0].lower()

        # Telegram 命令映射表
        if command == "/status" and self._has_telegram_alias("diagnostics.refresh", "/status"):
            action_id = "diagnostics.refresh"
        elif (
            command == "/project"
            and len(parts) >= 3
            and parts[1].lower() == "select"
            and self._has_telegram_alias("project.select", "/project select")
        ):
            action_id = "project.select"
            params = {"project_id": parts[2]}
        elif (
            command == "/approve"
            and len(parts) >= 3
            and self._has_telegram_alias("operator.approval.resolve", "/approve")
        ):
            action_id = "operator.approval.resolve"
            params = {"approval_id": parts[1], "mode": parts[2]}
        elif (
            command == "/cancel"
            and len(parts) >= 2
            and self._has_telegram_alias("session.interrupt", "/cancel")
        ):
            action_id = "session.interrupt"
            params = {"task_id": parts[1]}
        elif (
            command == "/retry"
            and len(parts) >= 2
            and self._has_telegram_alias("operator.task.retry", "/retry")
        ):
            action_id = "operator.task.retry"
            params = {"item_id": f"task:{parts[1]}"}
        elif command == "/backup" and self._has_telegram_alias("backup.create", "/backup"):
            label = " ".join(parts[1:]) if len(parts) >= 2 else ""
            action_id = "backup.create"
            params = {"label": label} if label else {}
        elif command == "/update" and len(parts) >= 2:
            mode = parts[1].lower()
            if mode == "dry-run" and self._has_telegram_alias("update.dry_run", "/update dry-run"):
                action_id = "update.dry_run"
            elif mode == "apply" and self._has_telegram_alias("update.apply", "/update apply"):
                action_id = "update.apply"
        elif (
            command == "/automation"
            and len(parts) >= 3
            and parts[1].lower() == "run"
            and self._has_telegram_alias("automation.run", "/automation run")
        ):
            action_id = "automation.run"
            params = {"job_id": parts[2]}
        elif command == "/work" and len(parts) >= 3:
            sub = parts[1].lower()
            action_map = {
                "cancel": "work.cancel",
                "retry": "work.retry",
                "delete": "work.delete",
                "escalate": "work.escalate",
            }
            if sub in action_map:
                aid = action_map[sub]
                if self._has_telegram_alias(aid, f"/work {sub}"):
                    action_id = aid
                    params = {"work_id": parts[2]}
        elif command == "/pipeline" and len(parts) >= 3:
            sub = parts[1].lower()
            if sub == "resume" and self._has_telegram_alias("pipeline.resume", "/pipeline resume"):
                action_id = "pipeline.resume"
                params = {"work_id": parts[2]}
            elif sub == "retry" and self._has_telegram_alias("pipeline.retry_node", "/pipeline retry"):
                action_id = "pipeline.retry_node"
                params = {"work_id": parts[2]}

        if not action_id:
            return None

        return ActionRequestEnvelope(
            request_id=str(ULID()),
            action_id=action_id,
            params=params,
            surface=ControlPlaneSurface.TELEGRAM,
            actor=ControlPlaneActor(
                actor_id=actor_id,
                actor_label=actor_label,
            ),
            context={"raw_text": raw},
        )

    def _has_telegram_alias(self, action_id: str, alias: str) -> bool:
        definition = self.get_action_definition(action_id)
        if definition is None:
            return False
        aliases = definition.surface_aliases.get("telegram", [])
        return alias in aliases
