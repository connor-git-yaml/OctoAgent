"""control_plane 拆分基础设施。

提供 ControlPlaneContext（共享依赖上下文）和 DomainServiceBase（domain service 基类）。
所有从 control_plane.py 拆分出的 domain service 都继承 DomainServiceBase。
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import structlog
from octoagent.core.models import (
    ActionRequestEnvelope,
    ActionResultEnvelope,
    ControlPlaneActionStatus,
    ControlPlaneResourceRef,
    ControlPlaneTargetRef,
    ProjectSelectorState,
)
from octoagent.core.store import StoreGroup
from octoagent.provider.dx.control_plane_state import ControlPlaneStateStore

log = structlog.get_logger()


class ControlPlaneActionError(RuntimeError):
    """control-plane 动作执行异常。"""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


class ControlPlaneContext:
    """所有 domain service 共享的依赖上下文。

    封装原 ControlPlaneService.__init__ 中持有的各项依赖，
    domain service 通过 ctx 访问而不是各自持有重复引用。

    service_registry: 延迟注册的 domain service 引用，用于跨 service 调用。
    """

    def __init__(
        self,
        *,
        project_root: Path,
        store_group: StoreGroup,
        sse_hub: Any = None,
        state_store: ControlPlaneStateStore | None = None,
        task_runner: Any = None,
        capability_pack_service: Any = None,
        delegation_plane_service: Any = None,
        import_workbench_service: Any = None,
        memory_console_service: Any = None,
        retrieval_platform_service: Any = None,
        operator_action_service: Any = None,
        operator_inbox_service: Any = None,
        policy_engine: Any = None,
        update_service: Any = None,
        automation_store: Any = None,
    ) -> None:
        self.project_root = project_root
        self.store_group = store_group
        self.sse_hub = sse_hub
        self.state_store = state_store or ControlPlaneStateStore(project_root)
        self.task_runner = task_runner
        self.capability_pack_service = capability_pack_service
        self.delegation_plane_service = delegation_plane_service
        self.import_workbench_service = import_workbench_service
        self.memory_console_service = memory_console_service
        self.retrieval_platform_service = retrieval_platform_service
        self.operator_action_service = operator_action_service
        self.operator_inbox_service = operator_inbox_service
        self.policy_engine = policy_engine
        self.update_service = update_service
        self.automation_store = automation_store
        # 跨 service 调用注册表（coordinator 构建后注入）
        self.service_registry: dict[str, Any] = {}


class DomainServiceBase:
    """domain service 基类，提供共享工具方法。

    子类继承此基类后：
    - 通过 self._ctx 访问所有共享依赖
    - 通过 self._stores 快捷访问 StoreGroup
    - 实现 action_routes() / document_routes() 向 coordinator 注册路由
    - 使用 _param_* / _completed_result / _rejected_result 等工具方法
    """

    def __init__(self, ctx: ControlPlaneContext) -> None:
        self._ctx = ctx
        self._stores = ctx.store_group

    def _get_service(self, name: str) -> Any:
        """从 service_registry 获取其他 domain service 实例。"""
        svc = self._ctx.service_registry.get(name)
        if svc is None:
            raise RuntimeError(f"service '{name}' 未在 service_registry 中注册")
        return svc

    def action_routes(self) -> dict[str, Any]:
        """子类实现：返回 {action_id: handler} 映射。"""
        return {}

    def document_routes(self) -> dict[str, Any]:
        """子类实现：返回 {section_id: getter} 映射。"""
        return {}

    # ------------------------------------------------------------------
    # 参数解析工具
    # ------------------------------------------------------------------

    def _param_str(
        self,
        params: Mapping[str, Any],
        key: str,
        *,
        default: str = "",
    ) -> str:
        value = params.get(key, default)
        if value is None:
            return default
        return str(value).strip()

    def _param_bool(
        self,
        params: Mapping[str, Any],
        key: str,
        *,
        default: bool = False,
    ) -> bool:
        value = params.get(key, default)
        if isinstance(value, bool):
            return value
        if isinstance(value, str):
            return value.strip().lower() in {"1", "true", "yes", "on"}
        return bool(value)

    def _param_int(
        self,
        params: Mapping[str, Any],
        key: str,
        *,
        default: int,
    ) -> int:
        value = params.get(key, default)
        if value in {None, ""}:
            return default
        try:
            return int(value)
        except (TypeError, ValueError) as exc:
            raise ControlPlaneActionError(
                "PARAM_INT_INVALID",
                f"{key} 必须是整数",
            ) from exc

    def _param_list(self, params: Mapping[str, Any], key: str) -> list[str]:
        value = params.get(key)
        if value is None:
            return []
        if isinstance(value, str):
            return [item.strip() for item in value.split(",") if item.strip()]
        if isinstance(value, list):
            return [str(item).strip() for item in value if str(item).strip()]
        raise ControlPlaneActionError("PARAM_LIST_INVALID", f"{key} 必须是 string/list")

    # ------------------------------------------------------------------
    # 结果构建工具
    # ------------------------------------------------------------------

    def _completed_result(
        self,
        *,
        request: ActionRequestEnvelope,
        code: str,
        message: str,
        data: dict[str, Any] | None = None,
        resource_refs: list[ControlPlaneResourceRef] | None = None,
        target_refs: list[ControlPlaneTargetRef] | None = None,
    ) -> ActionResultEnvelope:
        return ActionResultEnvelope(
            request_id=request.request_id,
            correlation_id=request.request_id,
            action_id=request.action_id,
            status=ControlPlaneActionStatus.COMPLETED,
            code=code,
            message=message,
            data=data or {},
            resource_refs=resource_refs or [],
            target_refs=target_refs or [],
        )

    def _deferred_result(
        self,
        *,
        request: ActionRequestEnvelope,
        code: str,
        message: str,
        correlation_id: str,
        data: dict[str, Any] | None = None,
        resource_refs: list[ControlPlaneResourceRef] | None = None,
        target_refs: list[ControlPlaneTargetRef] | None = None,
    ) -> ActionResultEnvelope:
        return ActionResultEnvelope(
            request_id=request.request_id,
            correlation_id=correlation_id,
            action_id=request.action_id,
            status=ControlPlaneActionStatus.DEFERRED,
            code=code,
            message=message,
            data=data or {},
            resource_refs=resource_refs or [],
            target_refs=target_refs or [],
        )

    def _rejected_result(
        self,
        *,
        request: ActionRequestEnvelope,
        code: str,
        message: str,
        target_refs: list[ControlPlaneTargetRef] | None = None,
    ) -> ActionResultEnvelope:
        return ActionResultEnvelope(
            request_id=request.request_id,
            correlation_id=request.request_id,
            action_id=request.action_id,
            status=ControlPlaneActionStatus.REJECTED,
            code=code,
            message=message,
            target_refs=target_refs or [],
        )

    def _resource_ref(self, resource_type: str, resource_id: str) -> ControlPlaneResourceRef:
        return ControlPlaneResourceRef(
            resource_type=resource_type,
            resource_id=resource_id,
            schema_version=1,
        )

    # ------------------------------------------------------------------
    # 异常构建
    # ------------------------------------------------------------------

    @staticmethod
    def _action_error(code: str, message: str) -> ControlPlaneActionError:
        """快捷构建 ControlPlaneActionError（供 raise self._action_error(…) 使用）。"""
        return ControlPlaneActionError(code, message)

    # ------------------------------------------------------------------
    # 数据规范化工具
    # ------------------------------------------------------------------

    @staticmethod
    def _normalize_text_list(value: Any) -> list[str]:
        if value is None:
            return []
        if isinstance(value, list):
            return [str(item).strip() for item in value if str(item).strip()]
        raw = str(value).strip()
        if not raw:
            return []
        if "\n" in raw:
            return [item.strip() for item in raw.splitlines() if item.strip()]
        if "," in raw:
            return [item.strip() for item in raw.split(",") if item.strip()]
        return [raw] if raw else []

    @staticmethod
    def _normalize_dict(value: Any) -> dict[str, Any]:
        if isinstance(value, dict):
            return dict(value)
        return {}

    # ------------------------------------------------------------------
    # Policy 共享查询（agent / setup / worker 都需要）
    # ------------------------------------------------------------------

    @staticmethod
    def _policy_catalog() -> list[tuple[str, str, Any, str, list[str]]]:
        """返回 (profile_id, label, PolicyProfile, risk_level, recommended_for)。"""
        from octoagent.policy import DEFAULT_PROFILE, PERMISSIVE_PROFILE, STRICT_PROFILE

        return [
            ("strict", "谨慎", STRICT_PROFILE, "warning", ["首次使用", "公网暴露", "高风险项目"]),
            ("default", "平衡", DEFAULT_PROFILE, "info", ["本地开发", "可信内网", "默认推荐"]),
            ("permissive", "自主", PERMISSIVE_PROFILE, "high", ["完全受信任环境", "高级用户"]),
        ]

    def _policy_profile_by_id(self, profile_id: str) -> Any | None:
        catalog = {
            item_id: profile
            for item_id, _, profile, _, _ in self._policy_catalog()
        }
        return catalog.get(str(profile_id).strip().lower())

    def _resolve_effective_policy_profile(
        self,
        project: Any | None,
    ) -> tuple[str, Any]:
        from octoagent.policy import DEFAULT_PROFILE

        if project is not None:
            metadata = getattr(project, "metadata", {}) or {}
            stored_profile_id = str(metadata.get("policy_profile_id", "")).strip().lower()
            stored_profile = self._policy_profile_by_id(stored_profile_id)
            if stored_profile is not None:
                return stored_profile_id, stored_profile
        if self._ctx.policy_engine is not None:
            runtime_profile = self._ctx.policy_engine.profile
            runtime_profile_id = str(runtime_profile.name).strip().lower() or "default"
            mapped = self._policy_profile_by_id(runtime_profile_id)
            if mapped is not None:
                return runtime_profile_id, mapped
        return "default", DEFAULT_PROFILE

    @staticmethod
    def _tool_profile_allowed(required: str, allowed: str) -> bool:
        ranking = {"minimal": 0, "standard": 1, "privileged": 2}
        return ranking.get(required, 1) <= ranking.get(allowed, 1)

    @staticmethod
    def _describe_policy_approval(profile: Any) -> str:
        """生成 policy profile 的审批策略描述。"""
        reversible = getattr(profile, "reversible_action", None)
        irreversible = getattr(profile, "irreversible_action", None)
        if reversible is not None and irreversible is not None:
            rev_val = reversible.value if hasattr(reversible, "value") else str(reversible)
            irr_val = irreversible.value if hasattr(irreversible, "value") else str(irreversible)
            if rev_val == "ask" and irr_val == "ask":
                return "可逆 / 不可逆操作都需要确认"
            if irr_val == "ask":
                return "仅不可逆操作需要确认"
        return "默认直接执行"

    async def _sync_policy_engine_for_project(self, project: Any) -> None:
        """同步 policy engine 的 runtime profile 到项目配置。"""
        if self._ctx.policy_engine is None:
            return
        metadata = getattr(project, "metadata", {}) or {}
        profile_id = str(metadata.get("policy_profile_id", "")).strip().lower()
        if not profile_id:
            return
        profile = self._policy_profile_by_id(profile_id)
        if profile is not None:
            self._ctx.policy_engine.profile = profile

    # ------------------------------------------------------------------
    # 共享查询
    # ------------------------------------------------------------------

    async def _resolve_selection(self):
        """解析当前选中的 project。

        返回 (state, project, None, fallback_reason)。
        逻辑与原 ControlPlaneService._resolve_selection 完全一致。
        """
        state = self._ctx.state_store.load()
        fallback_reason = ""
        selector = await self._stores.project_store.get_selector_state("web")
        project = (
            await self._stores.project_store.get_project(state.selected_project_id)
            if state.selected_project_id
            else None
        )
        if project is None and selector is not None:
            project = await self._stores.project_store.get_project(selector.active_project_id)
        if project is None:
            project = await self._stores.project_store.get_default_project()
            if project is not None and state.selected_project_id:
                fallback_reason = "selected project 不存在，已回退到 default project"

        if project is not None and state.selected_project_id != project.project_id:
            self._ctx.state_store.save(
                state.model_copy(
                    update={
                        "selected_project_id": project.project_id,
                        "updated_at": datetime.now(tz=UTC),
                    }
                )
            )
        if project is not None and (
            selector is None
            or selector.active_project_id != project.project_id
        ):
            await self._sync_web_project_selector_state(
                project=project,
                source="control_plane_sync",
                warnings=[fallback_reason] if fallback_reason else [],
            )
            await self._stores.conn.commit()
        return state, project, None, fallback_reason

    async def _sync_web_project_selector_state(
        self,
        *,
        project: Any,
        source: str,
        warnings: list[str] | None = None,
    ) -> None:
        """同步 web surface 的 project selector 状态。"""
        await self._stores.project_store.save_selector_state(
            ProjectSelectorState(
                selector_id="selector-web",
                surface="web",
                active_project_id=project.project_id,
                active_workspace_id="",
                source=source,
                warnings=list(warnings or []),
                updated_at=datetime.now(tz=UTC),
            )
        )

    @staticmethod
    def _matches_selected_scope(
        *,
        item_project_id: str | None,
        item_workspace_id: str | None,
        selected_project: Any | None,
    ) -> bool:
        """判断某个资源是否属于当前选中 project 的 scope。"""
        if selected_project is None:
            return not item_project_id
        if item_project_id and item_project_id != selected_project.project_id:
            return False
        return True
