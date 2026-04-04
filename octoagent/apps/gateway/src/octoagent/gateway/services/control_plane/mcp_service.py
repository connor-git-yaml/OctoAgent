"""Phase 5: MCP provider 领域服务。

从 control_plane.py 提取的 MCP provider catalog / install / uninstall / save / delete
以及 OAuth 相关 action handler。
"""

from __future__ import annotations

import asyncio
from collections import defaultdict
from collections.abc import Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import structlog
from octoagent.core.models import (
    ActionRequestEnvelope,
    ActionResultEnvelope,
    CapabilityPackDocument,
    ControlPlaneCapability,
    ControlPlaneDegradedState,
    ControlPlaneResourceRef,
    ControlPlaneSurface,
    ControlPlaneTargetRef,
    McpProviderCatalogDocument,
    McpProviderItem,
    SkillGovernanceDocument,
    SkillGovernanceItem,
    UpdateTriggerSource,
)
from octoagent.provider.auth.credentials import ApiKeyCredential
from octoagent.provider.auth.environment import detect_environment
from octoagent.provider.auth.oauth_flows import run_auth_code_pkce_flow
from octoagent.provider.auth.oauth_provider import OAuthProviderRegistry
from octoagent.provider.auth.profile import ProviderProfile
from octoagent.provider.auth.store import CredentialStore
from octoagent.provider.dx.config_wizard import load_config
from octoagent.provider.dx.litellm_generator import generate_litellm_config
from octoagent.provider.dx.runtime_activation import (
    RuntimeActivationError,
    RuntimeActivationService,
)
from pydantic import SecretStr

from ..mcp_registry import McpServerConfig
from ._base import ControlPlaneContext, DomainServiceBase

log = structlog.get_logger()


class McpDomainService(DomainServiceBase):
    """MCP provider catalog、install/uninstall、save/delete、OAuth action handler。"""

    def __init__(
        self,
        ctx: ControlPlaneContext,
        *,
        mcp_installer: Any | None = None,
        proxy_manager: Any | None = None,
    ) -> None:
        super().__init__(ctx)
        self._mcp_installer = mcp_installer
        self._proxy_manager = proxy_manager

    # ── resource producers ──────────────────────────────────────

    async def get_mcp_provider_catalog_document(self) -> McpProviderCatalogDocument:
        _, selected_project, _, _ = await self.ctx.resolve_selection()
        mcp_registry = (
            None
            if self.ctx.capability_pack_service is None
            else self.ctx.capability_pack_service.mcp_registry
        )
        if mcp_registry is None:
            return McpProviderCatalogDocument(
                active_project_id=selected_project.project_id if selected_project else "",
                active_workspace_id="",
                warnings=["MCP registry 尚未绑定，无法加载 MCP providers。"],
                degraded=ControlPlaneDegradedState(
                    is_degraded=True,
                    reasons=["mcp_registry_unavailable"],
                ),
            )
        servers = {item.server_name: item for item in mcp_registry.list_servers()}
        governance = await self._get_skill_governance_for_mcp(
            selected_project=selected_project,
        )
        governance_map = {item.item_id: item for item in governance.items}

        # Feature 058: 合并安装注册表数据
        install_records: dict[str, Any] = {}
        if self._mcp_installer is not None:
            install_records = {
                r.server_id: r for r in self._mcp_installer.list_installs()
            }

        items: list[McpProviderItem] = []
        for config in mcp_registry.list_configs():
            record = servers.get(config.name)
            governance_item = governance_map.get(f"mcp:{config.name}")
            install = install_records.get(config.name)
            items.append(
                McpProviderItem(
                    provider_id=config.name,
                    label=config.name,
                    description=record.error if record and record.error else config.command,
                    editable=True,
                    removable=True,
                    enabled=config.enabled,
                    status=record.status if record is not None else "unconfigured",
                    command=config.command,
                    args=list(config.args),
                    cwd=config.cwd,
                    env=dict(config.env),
                    mount_policy=str(config.mount_policy).strip().lower() or "auto_readonly",
                    tool_count=record.tool_count if record is not None else 0,
                    selection_item_id=f"mcp:{config.name}",
                    install_hint=governance_item.install_hint if governance_item else "",
                    error=record.error if record is not None else "",
                    warnings=(
                        []
                        if governance_item is None
                        else list(governance_item.missing_requirements)
                    ),
                    details={
                        "discovered_at": (
                            record.discovered_at.isoformat()
                            if record is not None and record.discovered_at is not None
                            else ""
                        )
                    },
                    install_source=str(install.install_source) if install else "",
                    install_version=install.version if install else "",
                    install_path=install.install_path if install else "",
                    installed_at=(
                        install.installed_at.isoformat() if install else ""
                    ),
                )
            )
        return McpProviderCatalogDocument(
            active_project_id=selected_project.project_id if selected_project is not None else "",
            active_workspace_id="",
            items=items,
            summary={
                "installed_count": len(items),
                "enabled_count": len([item for item in items if item.enabled]),
                "healthy_count": len([item for item in items if item.status == "available"]),
                "auto_installed_count": len(
                    [i for i in items if i.install_source and i.install_source != "manual"]
                ),
                "manual_count": len(
                    [i for i in items if not i.install_source or i.install_source == "manual"]
                ),
            },
            capabilities=[
                ControlPlaneCapability(
                    capability_id="mcp_provider.save",
                    label="手动添加 MCP Provider",
                    action_id="mcp_provider.save",
                ),
                ControlPlaneCapability(
                    capability_id="mcp_provider.install",
                    label="安装 MCP Provider",
                    action_id="mcp_provider.install",
                ),
                ControlPlaneCapability(
                    capability_id="mcp_provider.uninstall",
                    label="卸载 MCP Provider",
                    action_id="mcp_provider.uninstall",
                ),
            ],
            warnings=[] if items else ["当前没有已安装的 MCP providers。"],
            degraded=ControlPlaneDegradedState(
                is_degraded=not bool(items),
                reasons=["mcp_provider_catalog_empty"] if not items else [],
            ),
        )

    # ── action handlers ─────────────────────────────────────────

    async def handle_mcp_provider_install(
        self,
        request: ActionRequestEnvelope,
    ) -> ActionResultEnvelope:
        """启动 MCP server 异步安装任务。"""
        if self._mcp_installer is None:
            raise self._action_error("MCP_INSTALLER_UNAVAILABLE", "MCP Installer 未绑定")

        install_source = self._param_str(request.params, "install_source")
        if install_source not in {"npm", "pip"}:
            raise self._action_error(
                "MCP_INSTALL_SOURCE_INVALID",
                f"安装来源不合法: {install_source}（支持 npm/pip）",
            )
        package_name = self._param_str(request.params, "package_name")
        if not package_name:
            raise self._action_error("MCP_PACKAGE_NAME_REQUIRED", "包名不能为空")

        env = self._normalize_dict(request.params.get("env"))
        env = {str(k): str(v) for k, v in env.items() if str(k).strip()}

        try:
            task_id = await self._mcp_installer.install(
                install_source=install_source,
                package_name=package_name,
                env=env,
            )
        except ValueError as exc:
            err_msg = str(exc)
            if "已安装" in err_msg:
                raise self._action_error("MCP_SERVER_ALREADY_INSTALLED", err_msg) from exc
            if "格式不合法" in err_msg or "危险字符" in err_msg:
                raise self._action_error("MCP_PACKAGE_NAME_INVALID", err_msg) from exc
            raise self._action_error("MCP_INSTALL_FAILED", err_msg) from exc

        # 计算预期 server_id
        from ..mcp_installer import _slugify_server_id, InstallSource as _IS

        server_id = _slugify_server_id(_IS(install_source), package_name)

        return self._completed_result(
            request=request,
            code="MCP_INSTALL_STARTED",
            message="MCP server 安装已启动",
            data={"task_id": task_id, "server_id": server_id},
            resource_refs=[
                self._resource_ref("mcp_provider_catalog", "mcp-providers:catalog"),
            ],
        )

    async def handle_mcp_provider_install_status(
        self,
        request: ActionRequestEnvelope,
    ) -> ActionResultEnvelope:
        """查询安装任务进度。"""
        if self._mcp_installer is None:
            raise self._action_error("MCP_INSTALLER_UNAVAILABLE", "MCP Installer 未绑定")

        task_id = self._param_str(request.params, "task_id")
        if not task_id:
            raise self._action_error("MCP_INSTALL_TASK_NOT_FOUND", "task_id 不能为空")

        task = self._mcp_installer.get_install_status(task_id)
        if task is None:
            raise self._action_error("MCP_INSTALL_TASK_NOT_FOUND", "安装任务不存在")

        return self._completed_result(
            request=request,
            code="MCP_INSTALL_STATUS",
            message="安装状态查询成功",
            data={
                "task_id": task.task_id,
                "status": str(task.status),
                "progress_message": task.progress_message,
                "error": task.error,
                "result": task.result if task.result else None,
            },
        )

    async def handle_mcp_provider_uninstall(
        self,
        request: ActionRequestEnvelope,
    ) -> ActionResultEnvelope:
        """卸载已安装 MCP server。"""
        if self._mcp_installer is None:
            raise self._action_error("MCP_INSTALLER_UNAVAILABLE", "MCP Installer 未绑定")

        server_id = self._param_str(request.params, "server_id")
        if not server_id:
            raise self._action_error("MCP_SERVER_ID_REQUIRED", "server_id 不能为空")

        try:
            result = await self._mcp_installer.uninstall(server_id)
        except ValueError as exc:
            raise self._action_error(
                "MCP_SERVER_NOT_INSTALLED",
                str(exc),
            ) from exc

        return self._completed_result(
            request=request,
            code="MCP_SERVER_UNINSTALLED",
            message="MCP server 已卸载",
            data=result,
            resource_refs=[
                self._resource_ref("mcp_provider_catalog", "mcp-providers:catalog"),
                self._resource_ref("capability_pack", "capability:bundled"),
                self._resource_ref("skill_governance", "skills:governance"),
            ],
        )

    async def handle_mcp_provider_save(
        self,
        request: ActionRequestEnvelope,
    ) -> ActionResultEnvelope:
        if (
            self.ctx.capability_pack_service is None
            or self.ctx.capability_pack_service.mcp_registry is None
        ):
            raise self._action_error("MCP_REGISTRY_UNAVAILABLE", "MCP registry 未绑定")
        raw = request.params.get("provider")
        if not isinstance(raw, Mapping):
            raise self._action_error("MCP_PROVIDER_REQUIRED", "provider 必须是对象")
        provider_id = self._normalize_provider_id(
            self._param_str(raw, "provider_id") or self._param_str(raw, "label")
        )
        if not provider_id:
            raise self._action_error("MCP_PROVIDER_ID_REQUIRED", "provider_id 不能为空")
        command = self._param_str(raw, "command")
        if not command:
            raise self._action_error("MCP_PROVIDER_COMMAND_REQUIRED", "command 不能为空")
        mount_policy = self._param_str(raw, "mount_policy", default="auto_readonly").lower()
        if mount_policy not in {"explicit", "auto_readonly", "auto_all"}:
            raise self._action_error(
                "MCP_PROVIDER_MOUNT_POLICY_INVALID",
                "mount_policy 不合法",
            )
        config = McpServerConfig.model_validate(
            {
                "name": provider_id,
                "command": command,
                "args": self._normalize_text_list(raw.get("args")),
                "env": {
                    key: str(value)
                    for key, value in self._normalize_dict(raw.get("env")).items()
                    if str(key).strip()
                },
                "cwd": self._param_str(raw, "cwd"),
                "enabled": self._param_bool(raw, "enabled", default=True),
                "mount_policy": mount_policy,
            }
        )
        self.ctx.capability_pack_service.mcp_registry.save_config(config)
        await self.ctx.capability_pack_service.refresh()
        document = await self.get_mcp_provider_catalog_document()
        return self._completed_result(
            request=request,
            code="MCP_PROVIDER_SAVED",
            message="MCP provider 已保存。",
            data={
                "provider_id": provider_id,
                "installed_count": document.summary.get("installed_count", 0),
            },
            resource_refs=[
                self._resource_ref("mcp_provider_catalog", "mcp-providers:catalog"),
                self._resource_ref("capability_pack", "capability:bundled"),
                self._resource_ref("skill_governance", "skills:governance"),
            ],
        )

    async def handle_mcp_provider_delete(
        self,
        request: ActionRequestEnvelope,
    ) -> ActionResultEnvelope:
        if (
            self.ctx.capability_pack_service is None
            or self.ctx.capability_pack_service.mcp_registry is None
        ):
            raise self._action_error("MCP_REGISTRY_UNAVAILABLE", "MCP registry 未绑定")
        provider_id = self._normalize_provider_id(self._param_str(request.params, "provider_id"))
        if not provider_id:
            raise self._action_error("MCP_PROVIDER_ID_REQUIRED", "provider_id 不能为空")
        removed = self.ctx.capability_pack_service.mcp_registry.delete_config(provider_id)
        if not removed:
            raise self._action_error("MCP_PROVIDER_NOT_FOUND", "MCP provider 不存在")
        await self.ctx.capability_pack_service.refresh()
        return self._completed_result(
            request=request,
            code="MCP_PROVIDER_DELETED",
            message="MCP provider 已删除。",
            data={"provider_id": provider_id},
            resource_refs=[
                self._resource_ref("mcp_provider_catalog", "mcp-providers:catalog"),
                self._resource_ref("capability_pack", "capability:bundled"),
                self._resource_ref("skill_governance", "skills:governance"),
            ],
        )

    async def handle_provider_oauth_openai_codex(
        self,
        request: ActionRequestEnvelope,
    ) -> ActionResultEnvelope:
        env_name = self._param_str(request.params, "env_name", default="OPENAI_API_KEY")
        profile_name = self._param_str(
            request.params,
            "profile_name",
            default="openai-codex-default",
        )
        registry = OAuthProviderRegistry()
        provider_config = registry.get("openai-codex")
        if provider_config is None:
            raise self._action_error("OAUTH_PROVIDER_UNAVAILABLE", "未找到 OpenAI OAuth 配置")

        environment = detect_environment()
        if environment.use_manual_mode:
            raise self._action_error(
                "OAUTH_BROWSER_UNAVAILABLE",
                "当前环境无法直接打开浏览器，请先在本地桌面环境完成 OpenAI OAuth。",
            )

        credential = await run_auth_code_pkce_flow(
            config=provider_config,
            registry=registry,
            env=environment,
            use_gateway_callback=True,
        )
        store = self._credential_store()
        existing = store.get_profile(profile_name)
        now = datetime.now(tz=UTC)
        profile = ProviderProfile(
            name=profile_name,
            provider="openai-codex",
            auth_mode="oauth",
            credential=credential,
            is_default=(
                existing.is_default
                if existing is not None
                else store.get_default_profile() is None
            ),
            created_at=existing.created_at if existing is not None else now,
            updated_at=now,
        )
        store.set_profile(profile)
        self._write_env_values(
            self.ctx.project_root / ".env.litellm",
            {
                env_name: credential.access_token.get_secret_value(),
            },
        )

        # OAuth 成功后自动同步 litellm-config.yaml
        try:
            config = load_config(self.ctx.project_root)
            generate_litellm_config(config, self.ctx.project_root)
            log.info("oauth_litellm_config_synced")
        except Exception as exc:
            log.warning("oauth_litellm_config_sync_failed", error=str(exc))

        activation_data = await self._activate_runtime_after_config_change(
            request=request,
            failure_code="OPENAI_OAUTH_ACTIVATION_FAILED",
            failure_prefix="OpenAI Auth 已连接，但真实模型激活失败",
            raise_on_failure=False,
        )

        message = "OpenAI Auth 已连接，已写入本地凭证。"
        runtime_message = str(activation_data.get("runtime_reload_message", "")).strip()
        if runtime_message:
            message = runtime_message

        return self._completed_result(
            request=request,
            code="OPENAI_OAUTH_CONNECTED",
            message=message,
            data={
                "provider_id": "openai-codex",
                "profile_name": profile_name,
                "env_name": env_name,
                "expires_at": credential.expires_at.isoformat(),
                "account_id": credential.account_id or "",
                "activation": activation_data,
            },
            resource_refs=[
                self._resource_ref("setup_governance", "setup:governance"),
                self._resource_ref("diagnostics_summary", "diagnostics:runtime"),
            ],
            target_refs=[
                ControlPlaneTargetRef(
                    target_type="provider",
                    target_id="openai-codex",
                    label="OpenAI Codex",
                )
            ],
        )

    # ── private helpers ──────────────────────────────────────────

    async def _get_skill_governance_for_mcp(
        self,
        *,
        selected_project: Any | None,
    ) -> SkillGovernanceDocument:
        """获取 skill governance 用于 catalog 合并展示。

        这里直接委托回 ctx.host（原 ControlPlaneService），
        因为 skill governance 生成逻辑横跨 capability pack / policy 等多个子系统。
        """
        return await self.ctx.host.get_skill_governance_document(
            selected_project=selected_project,
        )

    def _credential_store(self) -> CredentialStore:
        return CredentialStore(store_path=self.ctx.project_root / "auth-profiles.json")

    def _write_env_values(self, path: Path, updates: Mapping[str, str]) -> None:
        normalized = {
            str(key).strip(): str(value)
            for key, value in updates.items()
            if str(key).strip() and str(value).strip()
        }
        if not normalized:
            return
        existing_lines = path.read_text(encoding="utf-8").splitlines() if path.exists() else []
        rendered: list[str] = []
        seen_keys: set[str] = set()
        for line in existing_lines:
            stripped = line.strip()
            if not stripped or stripped.startswith("#") or "=" not in line:
                rendered.append(line)
                continue
            key, _ = line.split("=", 1)
            env_name = key.strip()
            if env_name in normalized:
                rendered.append(f"{env_name}={normalized[env_name]}")
                seen_keys.add(env_name)
            else:
                rendered.append(line)
        for env_name, value in normalized.items():
            if env_name not in seen_keys:
                rendered.append(f"{env_name}={value}")
        path.parent.mkdir(parents=True, exist_ok=True)
        content = "\n".join(rendered).rstrip()
        path.write_text(f"{content}\n" if content else "", encoding="utf-8")
        path.chmod(0o600)

    async def _activate_runtime_after_config_change(
        self,
        *,
        request: ActionRequestEnvelope,
        failure_code: str,
        failure_prefix: str,
        raise_on_failure: bool,
    ) -> dict[str, Any]:
        """激活 LiteLLM Proxy，并在托管实例中安排 runtime reload。"""
        activation_service = RuntimeActivationService(self.ctx.project_root)
        try:
            activation = await activation_service.start_proxy()
        except RuntimeActivationError as exc:
            if raise_on_failure:
                raise self._action_error(
                    failure_code,
                    f"{failure_prefix}：{exc}",
                ) from exc
            return {
                "project_root": str(self.ctx.project_root),
                "source_root": "",
                "compose_file": "",
                "proxy_url": "",
                "managed_runtime": activation_service.has_managed_runtime(),
                "warnings": [str(exc)],
                "runtime_reload_mode": "activation_failed",
                "runtime_reload_message": f"{failure_prefix}：{exc}",
                "activation_succeeded": False,
            }

        activation_data: dict[str, Any] = {
            "project_root": activation.project_root,
            "source_root": activation.source_root,
            "compose_file": activation.compose_file,
            "proxy_url": activation.proxy_url,
            "managed_runtime": activation.managed_runtime,
            "warnings": list(activation.warnings),
            "runtime_reload_mode": "none",
            "runtime_reload_message": "真实模型连接已准备完成。",
            "activation_succeeded": True,
        }

        update_service = self.ctx.update_service
        if activation.managed_runtime and update_service is not None:
            if request.surface == ControlPlaneSurface.CLI:
                await update_service.restart(
                    trigger_source=self._map_update_source(request.surface)
                )
                activation_data["runtime_reload_mode"] = "managed_restart_completed"
                activation_data["runtime_reload_message"] = (
                    "已自动重启托管实例，真实模型会在新进程里生效。"
                )
            else:
                asyncio.create_task(
                    self._restart_runtime_after_delay(
                        delay_seconds=2.0,
                        trigger_source=self._map_update_source(request.surface),
                    )
                )
                activation_data["runtime_reload_mode"] = "managed_restart_scheduled"
                activation_data["runtime_reload_message"] = (
                    "已启动 LiteLLM Proxy，当前实例会在几秒内自动重启并切到真实模型。"
                )
        else:
            activation_data["runtime_reload_mode"] = "manual_restart_required"
            activation_data["runtime_reload_message"] = (
                "LiteLLM Proxy 已启动；如果当前 Gateway 正在运行，请手动重启后再开始真实对话。"
            )
        return activation_data

    async def _restart_runtime_after_delay(
        self,
        *,
        delay_seconds: float,
        trigger_source: UpdateTriggerSource,
    ) -> None:
        update_service = self.ctx.update_service
        if update_service is None:
            return
        await asyncio.sleep(delay_seconds)
        try:
            await update_service.restart(trigger_source=trigger_source)
        except Exception as exc:  # pragma: no cover
            log.warning(
                "mcp_oauth_restart_failed",
                error_type=type(exc).__name__,
                error=str(exc),
            )

    @staticmethod
    def _map_update_source(surface: ControlPlaneSurface) -> UpdateTriggerSource:
        mapping = {
            ControlPlaneSurface.WEB: UpdateTriggerSource.WEB,
            ControlPlaneSurface.CLI: UpdateTriggerSource.CLI,
            ControlPlaneSurface.TELEGRAM: UpdateTriggerSource.TELEGRAM,
        }
        return mapping.get(surface, UpdateTriggerSource.SYSTEM)

    @staticmethod
    def _normalize_provider_id(value: str) -> str:
        lowered = value.strip().lower()
        if not lowered:
            return ""
        chars: list[str] = []
        previous_dash = False
        for char in lowered:
            if char.isascii() and char.isalnum():
                chars.append(char)
                previous_dash = False
                continue
            if previous_dash:
                continue
            chars.append("-")
            previous_dash = True
        return "".join(chars).strip("-")
