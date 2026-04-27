"""Feature 082 P2：Bootstrap 完成路径编排器。

历史问题：BootstrapSession 创建后状态为 PENDING，但**没有显式完成路径**接入主流程。
``mark_onboarding_completed()`` 几乎从未被调用；Profile 生成的画像只写入 Memory SoR，
**不回填 OwnerProfile 表** → 用户引导完后 OwnerProfile 仍是默认值。

P2 修复：``BootstrapSessionOrchestrator.complete_bootstrap()`` 是统一完成入口：
1. 验证 BootstrapSession 状态可完成（PENDING / IN_PROGRESS）
2. 应用字段冲突策略，回填 OwnerProfile 表
3. mark_onboarding_completed() 持久化 .onboarding-state.json
4. BootstrapSession.status = COMPLETED + completed_at = now
5. 返回结果供 LLM 工具 / 测试展示

字段冲突策略（顺序从高到低）：
- 用户显式设置（``last_synced_from_profile_at`` 之后改过 ``updated_at``）→ 不覆盖
- 当前为空（"" / [] / None）→ 覆盖为新值
- 当前为历史伪默认 ``"你"`` → 覆盖为新值
- 否则（用户已显式设过且非默认）→ 不覆盖

调用方：
- ``builtin_tools/bootstrap_tools.py:bootstrap.complete()`` LLM 工具（Agent 引导完后调用）
- ``dx/config_commands.py:octo bootstrap migrate-082``（P4 检测/重置后回填）
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import structlog

from octoagent.core.behavior_workspace import mark_onboarding_completed
from octoagent.core.models.agent_context import (
    BootstrapSession,
    BootstrapSessionStatus,
    OwnerProfile,
)
from octoagent.core.store.agent_context_store import SqliteAgentContextStore

log = structlog.get_logger()


# OwnerProfile 字段中由 bootstrap 可填充的列表（按 spec FR-3 + plan §4.3）
_BOOTSTRAP_OWNER_FIELDS: tuple[str, ...] = (
    "preferred_address",
    "working_style",
    "interaction_preferences",
    "boundary_notes",
    "timezone",
    "locale",
    "display_name",
)


@dataclass(slots=True)
class BootstrapCompletionResult:
    """编排器输出。"""

    bootstrap_id: str
    onboarding_completed_at: str | None = None
    """``.onboarding-state.json`` 中标记的完成时间（ISO8601）；None = 已完成则不重复写。"""

    owner_profile_updated: bool = False
    fields_updated: list[str] = field(default_factory=list)
    fields_skipped: list[str] = field(default_factory=list)
    """用户已显式设置过 → 保留原值不覆盖；告诉调用方为什么没生效。"""

    user_md_written: bool = False
    """USER.md 是否被 UserMdRenderer 实际写入（OwnerProfile 实质填充时才写）。"""

    user_md_path: str | None = None
    """USER.md 写入的实际路径；未写入时为 None。"""

    warnings: list[str] = field(default_factory=list)

    def to_payload(self) -> dict[str, Any]:
        return {
            "bootstrap_id": self.bootstrap_id,
            "onboarding_completed_at": self.onboarding_completed_at,
            "owner_profile_updated": self.owner_profile_updated,
            "fields_updated": list(self.fields_updated),
            "fields_skipped": list(self.fields_skipped),
            "user_md_written": self.user_md_written,
            "user_md_path": self.user_md_path,
            "warnings": list(self.warnings),
        }


def _is_pseudo_default_value(field_name: str, value: Any) -> bool:
    """判定字段当前值是否是"伪默认"（视为可被覆盖）。

    - preferred_address: ``""`` 或 ``"你"`` 都视为伪默认
    - 字符串字段：``""`` 视为伪默认
    - 列表字段：``[]`` 视为伪默认
    - timezone="UTC" / locale="zh-CN"：仍是地理默认，**视为伪默认可被覆盖**
      （首次引导时这些是兜底值，用户答了真实地点应该被记录）
    - display_name="Owner"：兜底默认，可被覆盖
    """
    if field_name == "preferred_address":
        return value in ("", "你")
    if field_name == "timezone":
        return value in ("", "UTC")
    if field_name == "locale":
        return value in ("", "zh-CN")
    if field_name == "display_name":
        return value in ("", "Owner")
    if isinstance(value, str):
        return value == ""
    if isinstance(value, list):
        return len(value) == 0
    return value is None


def _apply_field_conflict_strategy(
    current: OwnerProfile,
    profile_updates: dict[str, Any],
) -> tuple[dict[str, Any], list[str], list[str]]:
    """应用字段冲突策略，返回 (apply_dict, updated_fields, skipped_fields)。

    策略：
    - last_synced_from_profile_at 存在但比 updated_at 早 → 用户在同步后改过 → 跳过
    - 当前值是伪默认 → 覆盖为新值
    - 当前已是用户显式值 → 跳过（保留用户输入）
    """
    apply_dict: dict[str, Any] = {}
    updated: list[str] = []
    skipped: list[str] = []

    last_synced = current.last_synced_from_profile_at
    user_modified_after_sync = bool(
        last_synced is not None and current.updated_at > last_synced
    )

    for field_name, new_value in profile_updates.items():
        if field_name not in _BOOTSTRAP_OWNER_FIELDS:
            continue
        if new_value is None:
            continue
        # 列表字段：空列表不算 update
        if isinstance(new_value, list) and len(new_value) == 0:
            continue
        # 字符串字段：空串不算 update
        if isinstance(new_value, str) and new_value == "":
            continue

        current_value = getattr(current, field_name)

        # 用户在上次同步后显式改过 → 严格保留
        if user_modified_after_sync and not _is_pseudo_default_value(field_name, current_value):
            skipped.append(field_name)
            continue

        # 当前是伪默认 → 覆盖
        if _is_pseudo_default_value(field_name, current_value):
            apply_dict[field_name] = new_value
            updated.append(field_name)
            continue

        # 当前是用户显式值（且无 last_synced 或同步在 updated 之后）→ 仍跳过保护用户输入
        skipped.append(field_name)

    return apply_dict, updated, skipped


class BootstrapSessionOrchestrator:
    """Bootstrap 完成路径统一编排器。"""

    def __init__(
        self,
        agent_context_store: SqliteAgentContextStore,
        project_root: Path,
    ) -> None:
        self._store = agent_context_store
        self._root = project_root.resolve()

    async def complete_bootstrap(
        self,
        bootstrap_id: str,
        *,
        profile_updates: dict[str, Any] | None = None,
    ) -> BootstrapCompletionResult:
        """触发 bootstrap 完成。

        Args:
            bootstrap_id: BootstrapSession ID
            profile_updates: 由 Agent 从用户回答中抽取的 OwnerProfile 字段 dict
                （如 ``{"preferred_address": "Connor", "timezone": "Asia/Shanghai"}``）；
                未传时仅做状态机标记，不更新 OwnerProfile。

        Returns:
            ``BootstrapCompletionResult`` 含每个字段的 updated/skipped 状态。
        """
        result = BootstrapCompletionResult(bootstrap_id=bootstrap_id)

        # 1. 加载 BootstrapSession
        session = await self._store.get_bootstrap_session(bootstrap_id)
        if session is None:
            result.warnings.append(f"BootstrapSession {bootstrap_id!r} 不存在")
            return result

        if session.status == BootstrapSessionStatus.COMPLETED:
            result.warnings.append(
                f"BootstrapSession {bootstrap_id!r} 已是 COMPLETED 状态；不重复完成"
            )
            return result

        # 2. 字段回填到 OwnerProfile
        if profile_updates and session.owner_profile_id:
            owner_profile = await self._store.get_owner_profile(session.owner_profile_id)
            if owner_profile is not None:
                apply_dict, updated_fields, skipped_fields = _apply_field_conflict_strategy(
                    owner_profile, profile_updates,
                )
                result.fields_updated = updated_fields
                result.fields_skipped = skipped_fields

                if apply_dict:
                    now = datetime.now(tz=UTC)
                    updated_profile = owner_profile.model_copy(
                        update={
                            **apply_dict,
                            "last_synced_from_profile_at": now,
                            "updated_at": now,
                            "version": owner_profile.version + 1,
                        }
                    )
                    await self._store.save_owner_profile(updated_profile)
                    result.owner_profile_updated = True
                    log.info(
                        "bootstrap_owner_profile_synced",
                        bootstrap_id=bootstrap_id,
                        owner_profile_id=session.owner_profile_id,
                        fields_updated=updated_fields,
                        fields_skipped=skipped_fields,
                    )
            else:
                result.warnings.append(
                    f"OwnerProfile {session.owner_profile_id!r} 不存在，跳过回填"
                )

        # 3. USER.md 渲染（Feature 082 P3）：基于刚回填的 OwnerProfile 重新写
        # 仅在 owner_profile_updated 时触发——避免覆盖用户手工 USER.md
        if result.owner_profile_updated and session.owner_profile_id:
            try:
                # 重新读最新 OwnerProfile（含刚同步的字段）
                from .user_md_renderer import UserMdRenderer

                fresh_profile = await self._store.get_owner_profile(session.owner_profile_id)
                renderer = UserMdRenderer(self._root)
                _render_result, written_path = renderer.render_and_write(fresh_profile)
                if written_path is not None:
                    result.user_md_written = True
                    result.user_md_path = str(written_path)
            except Exception as exc:
                result.warnings.append(f"USER.md 渲染失败：{exc}")
                log.warning(
                    "bootstrap_user_md_render_failed",
                    bootstrap_id=bootstrap_id,
                    error_type=type(exc).__name__,
                    error=str(exc),
                )

        # 4. 标记 .onboarding-state.json 完成
        try:
            state = mark_onboarding_completed(self._root)
            result.onboarding_completed_at = state.onboarding_completed_at
        except Exception as exc:
            result.warnings.append(f"标记 onboarding-state 失败：{exc}")
            log.warning(
                "bootstrap_mark_onboarding_completed_failed",
                bootstrap_id=bootstrap_id,
                error_type=type(exc).__name__,
                error=str(exc),
            )

        # 5. 更新 BootstrapSession.status = COMPLETED
        completed_session = session.model_copy(
            update={
                "status": BootstrapSessionStatus.COMPLETED,
                "completed_at": datetime.now(tz=UTC),
                "updated_at": datetime.now(tz=UTC),
            }
        )
        await self._store.save_bootstrap_session(completed_session)

        log.info(
            "bootstrap_completed",
            bootstrap_id=bootstrap_id,
            owner_profile_updated=result.owner_profile_updated,
            fields_updated=result.fields_updated,
        )
        return result
