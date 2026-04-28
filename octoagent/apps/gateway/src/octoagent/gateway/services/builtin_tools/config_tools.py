"""config_tools：配置管理与快速连接工具（6 个）。

工具列表：
- config.inspect
- config.add_provider
- config.set_model_alias
- config.sync
- setup.review
- setup.quick_connect
"""

from __future__ import annotations

import json
from typing import Any

from pydantic import BaseModel

from octoagent.tooling import SideEffectLevel, reflect_tool_schema, tool_contract
from octoagent.gateway.harness.tool_registry import ToolEntry
from octoagent.gateway.harness.tool_registry import register as _registry_register
from octoagent.core.models.tool_results import (
    ConfigAddProviderResult,
    ConfigSetModelAliasResult,
    ConfigSyncResult,
    SetupQuickConnectResult,
)

from ._deps import ToolDeps

# 各工具 entrypoints 声明（Feature 084 D1 根治 + Codex F2 收紧）
#
# 设计原则：写配置 / 凭证 / 模型连接的工具默认仅 agent_runtime 可见。
# Web 入口（owner 直接对话）需要 Approval Gate 才能调这些工具——Phase 3 上线
# ApprovalGate 后再开 web。当前阶段（Phase 1 末）只把只读 inspect 放 web。
#
# 例外：setup.review 是审视性操作（不改 yaml / 不写凭证），可以 web 看；
#      但 setup.quick_connect 会真实写 yaml + 凭证 → 必须经 Approval（Phase 3）
_TOOL_ENTRYPOINTS: dict[str, frozenset[str]] = {
    "config.inspect":         frozenset({"agent_runtime", "web"}),  # 只读
    "config.add_provider":    frozenset({"agent_runtime"}),          # 写 yaml → web 待 Phase 3 ApprovalGate
    "config.set_model_alias": frozenset({"agent_runtime"}),          # 写 yaml → web 待 Phase 3
    "config.sync":            frozenset({"agent_runtime"}),          # 写 yaml + LiteLLM → web 待 Phase 3
    "setup.review":           frozenset({"agent_runtime", "web"}),   # 只读审视
    "setup.quick_connect":    frozenset({"agent_runtime"}),          # 写 yaml + 凭证 → web 待 Phase 3
}


def _parse_setup_draft_json(raw: str) -> dict[str, Any]:
    text = str(raw or "").strip()
    if not text:
        return {}
    try:
        payload = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ValueError(f"draft_json 不是合法 JSON：{exc.msg}") from exc
    if not isinstance(payload, dict):
        raise ValueError("draft_json 必须是 object JSON")
    return payload


async def register(broker, deps: ToolDeps) -> None:
    """注册所有配置管理工具。"""
    from octoagent.gateway.services.config.config_wizard import load_config as _load_config
    from octoagent.gateway.services.config.config_wizard import save_config as _save_config

    @tool_contract(
        name="config.inspect",
        side_effect_level=SideEffectLevel.NONE,
        tool_group="config",
        tags=["config", "provider", "model", "inspect"],
        manifest_ref="builtin://config.inspect",
        metadata={
            "entrypoints": ["agent_runtime", "web"],
        },
    )
    async def config_inspect(section: str = "") -> str:
        """读取 octoagent.yaml 配置。section 可选: providers, model_aliases, memory, channels, runtime, 留空返回全部。"""
        config = _load_config(deps.project_root)
        if config is None:
            return json.dumps(
                {"error": "CONFIG_NOT_FOUND", "message": "octoagent.yaml 不存在或解析失败"},
                ensure_ascii=False,
            )
        data = config.model_dump(mode="json")
        section = section.strip().lower()
        if section and section in data:
            return json.dumps({section: data[section]}, ensure_ascii=False, indent=2)
        if section:
            return json.dumps(
                {"error": "UNKNOWN_SECTION", "message": f"未知 section: {section}", "available": list(data.keys())},
                ensure_ascii=False,
            )
        return json.dumps(data, ensure_ascii=False, indent=2)

    @tool_contract(
        name="config.add_provider",
        side_effect_level=SideEffectLevel.REVERSIBLE,
        tool_group="config",
        tags=["config", "provider", "add"],
        manifest_ref="builtin://config.add_provider",
        produces_write=True,
        metadata={
            "entrypoints": ["agent_runtime", "web"],
        },
    )
    async def config_add_provider(
        provider_id: str,
        name: str = "",
        auth_type: str = "",
        api_key_env: str = "",
        base_url: str = "",
        enabled: bool | None = None,
        clear_base_url: bool = False,
    ) -> ConfigAddProviderResult:
        """添加或更新一个 LLM Provider 到 octoagent.yaml（单一事实源）。修改后需执行 config.sync 重新生成衍生配置，或使用 setup.quick_connect 一键保存并启用。"""
        config = _load_config(deps.project_root)
        if config is None:
            return ConfigAddProviderResult(
                status="rejected",
                target="octoagent.yaml",
                provider_id="",
                action="added",
                reason="CONFIG_NOT_FOUND: octoagent.yaml 不存在",
            )
        provider_id = provider_id.strip().lower()
        if not provider_id:
            return ConfigAddProviderResult(
                status="rejected",
                target="octoagent.yaml",
                provider_id="",
                action="added",
                reason="MISSING_PARAM: provider_id 不能为空",
            )
        existing_provider = next((p for p in config.providers if p.id == provider_id), None)
        resolved_name = name.strip() or (
            existing_provider.name if existing_provider is not None else provider_id
        )
        resolved_auth_type = auth_type.strip() or (
            existing_provider.auth_type if existing_provider is not None else "api_key"
        )
        resolved_api_key_env = api_key_env.strip() or (
            existing_provider.api_key_env
            if existing_provider is not None
            else f"{provider_id.upper().replace('-', '_')}_API_KEY"
        )
        resolved_base_url = ""
        if clear_base_url:
            resolved_base_url = ""
        elif base_url.strip():
            resolved_base_url = base_url.strip()
        elif existing_provider is not None:
            resolved_base_url = existing_provider.base_url
        resolved_enabled = (
            enabled
            if enabled is not None
            else (existing_provider.enabled if existing_provider is not None else True)
        )

        from octoagent.gateway.services.config.config_schema import ProviderEntry
        new_entry = ProviderEntry(
            id=provider_id,
            name=resolved_name,
            auth_type=resolved_auth_type,
            api_key_env=resolved_api_key_env,
            enabled=resolved_enabled,
            base_url=resolved_base_url,
        )
        # 更新或追加
        found = False
        for i, p in enumerate(config.providers):
            if p.id == provider_id:
                config.providers[i] = new_entry
                found = True
                break
        if not found:
            config.providers.append(new_entry)

        from datetime import date
        config.updated_at = date.today().isoformat()
        _save_config(config, deps.project_root)
        action = "updated" if found else "added"
        return ConfigAddProviderResult(
            status="written",
            target="octoagent.yaml",
            provider_id=provider_id,
            action=action,  # type: ignore[arg-type]
            hint=(
                "执行 config.sync 重新生成 litellm-config.yaml；"
                "如需保存并启用真实模型，可继续执行 setup.quick_connect；"
                "也可使用 Web 设置页或 CLI `octo setup`。"
            ),
        )

    @tool_contract(
        name="config.set_model_alias",
        side_effect_level=SideEffectLevel.REVERSIBLE,
        tool_group="config",
        tags=["config", "model", "alias", "set"],
        manifest_ref="builtin://config.set_model_alias",
        produces_write=True,
        metadata={
            "entrypoints": ["agent_runtime", "web"],
        },
    )
    async def config_set_model_alias(
        alias: str,
        provider: str,
        model: str,
        description: str = "",
        thinking_level: str = "",
    ) -> ConfigSetModelAliasResult:
        """设置模型别名（如 main, cheap）到具体 Provider + 模型，写入 octoagent.yaml（单一事实源）。修改后需执行 config.sync 重新生成衍生配置，或使用 setup.quick_connect 一键保存并启用。"""
        config = _load_config(deps.project_root)
        if config is None:
            return ConfigSetModelAliasResult(
                status="rejected",
                target="octoagent.yaml",
                alias="",
                provider="",
                model="",
                reason="CONFIG_NOT_FOUND: octoagent.yaml 不存在",
            )
        alias = alias.strip().lower()
        if not alias or not provider.strip() or not model.strip():
            return ConfigSetModelAliasResult(
                status="rejected",
                target="octoagent.yaml",
                alias=alias,
                provider=provider,
                model=model,
                reason="MISSING_PARAM: alias, provider, model 都不能为空",
            )
        # 校验 provider 存在
        provider_ids = {p.id for p in config.providers}
        if provider.strip() not in provider_ids:
            return ConfigSetModelAliasResult(
                status="rejected",
                target="octoagent.yaml",
                alias=alias,
                provider=provider.strip(),
                model=model.strip(),
                reason=f"UNKNOWN_PROVIDER: Provider '{provider}' 不存在，可用: {sorted(provider_ids)}",
            )
        from octoagent.gateway.services.config.config_schema import ModelAlias as _ModelAlias
        kwargs: dict[str, Any] = {
            "provider": provider.strip(),
            "model": model.strip(),
        }
        if description.strip():
            kwargs["description"] = description.strip()
        if thinking_level.strip():
            kwargs["thinking_level"] = thinking_level.strip()
        config.model_aliases[alias] = _ModelAlias(**kwargs)

        from datetime import date
        config.updated_at = date.today().isoformat()
        _save_config(config, deps.project_root)
        return ConfigSetModelAliasResult(
            status="written",
            target="octoagent.yaml",
            alias=alias,
            provider=provider.strip(),
            model=model.strip(),
            hint=(
                "执行 config.sync 重新生成 litellm-config.yaml；"
                "如需保存并启用真实模型，可继续执行 setup.quick_connect；"
                "也可使用 Web 设置页或 CLI `octo setup`。"
            ),
        )

    @tool_contract(
        name="config.sync",
        side_effect_level=SideEffectLevel.REVERSIBLE,
        tool_group="config",
        tags=["config", "sync", "litellm"],
        manifest_ref="builtin://config.sync",
        produces_write=True,
        metadata={
            "entrypoints": ["agent_runtime", "web"],
        },
    )
    async def config_sync() -> ConfigSyncResult:
        """从 octoagent.yaml（单一事实源）重新生成 litellm-config.yaml（衍生配置）。等同于 CLI `octo config sync`。注意：本工具只同步衍生配置，不会自动重启 runtime 或切换到真实模型；如需一键保存并启用，请使用 setup.quick_connect 或 CLI `octo setup`。"""
        try:
            config = _load_config(deps.project_root)
            if config is None:
                return ConfigSyncResult(
                    status="rejected",
                    target="octoagent.yaml",
                    reason="CONFIG_NOT_FOUND: octoagent.yaml 不存在",
                )
            # Feature 081 P4：不再生成 litellm-config.yaml；只做基础校验
            enabled_providers = [p.id for p in config.providers if p.enabled]
            enabled_aliases = [
                k for k, v in config.model_aliases.items()
                if any(p.id == v.provider and p.enabled for p in config.providers)
            ]
            return ConfigSyncResult(
                status="written",
                target="octoagent.yaml",
                enabled_providers=enabled_providers,
                enabled_aliases=enabled_aliases,
            )
        except Exception as exc:
            return ConfigSyncResult(
                status="rejected",
                target="octoagent.yaml",
                reason=f"SYNC_FAILED: {exc}",
            )

    @tool_contract(
        name="setup.review",
        side_effect_level=SideEffectLevel.NONE,
        tool_group="setup",
        tags=["setup", "review", "config"],
        manifest_ref="builtin://setup.review",
        metadata={
            "entrypoints": ["agent_runtime", "web"],
        },
    )
    async def setup_review(draft_json: str = "{}") -> str:
        """调用 canonical setup.review 检查 draft 是否可保存。draft_json 需为 JSON object。"""
        try:
            draft = _parse_setup_draft_json(draft_json)
        except ValueError as exc:
            return json.dumps(
                {"error": "INVALID_DRAFT_JSON", "message": str(exc)},
                ensure_ascii=False,
            )

        from octoagent.provider.dx.setup_governance_adapter import (
            LocalSetupGovernanceAdapter,
        )

        adapter = LocalSetupGovernanceAdapter(deps.project_root)
        prepared = await adapter.prepare_wizard_draft(draft)
        result = await adapter.review(prepared)
        payload: dict[str, Any] = {
            "success": str(result.status) == "completed",
            "status": str(result.status),
            "code": result.code,
            "message": result.message,
        }
        if isinstance(result.data, dict):
            if isinstance(result.data.get("review"), dict):
                payload["review"] = result.data["review"]
            if result.data.get("warnings"):
                payload["warnings"] = result.data["warnings"]
        if not payload["success"]:
            payload["error"] = result.code
        return json.dumps(payload, ensure_ascii=False)

    @tool_contract(
        name="setup.quick_connect",
        side_effect_level=SideEffectLevel.REVERSIBLE,
        tool_group="setup",
        tags=["setup", "quick_connect", "activation"],
        manifest_ref="builtin://setup.quick_connect",
        produces_write=True,
        metadata={
            "entrypoints": ["agent_runtime", "web"],
        },
    )
    async def setup_quick_connect(draft_json: str) -> SetupQuickConnectResult:
        """保存 Provider 凭证并激活模型连接。

        用户提供 API Key 后，通过此工具完成凭证持久化和 runtime activation。
        这是安全的凭证注入通道——凭证写入 .env.litellm（不进版本管理），
        不经过 behavior files 或 LLM 上下文。

        Args:
            draft_json: JSON 对象，包含 providers 和 model_aliases 配置。

        示例（添加 SiliconFlow provider 并注入 API Key）:
            setup.quick_connect(draft_json='{
                "providers": [{
                    "id": "siliconflow",
                    "name": "SiliconFlow",
                    "auth_type": "api_key",
                    "api_key_env": "SILICONFLOW_API_KEY",
                    "api_key_value": "sk-xxx实际密钥xxx",
                    "base_url": "https://api.siliconflow.cn/v1",
                    "enabled": true
                }],
                "model_aliases": {
                    "cheap": {
                        "provider": "siliconflow",
                        "model": "Qwen/Qwen3.5-35B-A3B"
                    }
                }
            }')
        """
        try:
            draft = _parse_setup_draft_json(draft_json)
        except ValueError as exc:
            return SetupQuickConnectResult(
                status="rejected",
                target="octoagent.yaml",
                reason=f"INVALID_DRAFT_JSON: {exc}",
            )

        from octoagent.provider.dx.setup_governance_adapter import (
            LocalSetupGovernanceAdapter,
        )

        adapter = LocalSetupGovernanceAdapter(deps.project_root)
        prepared = await adapter.prepare_wizard_draft(draft)
        result = await adapter.quick_connect(prepared)
        is_success = str(result.status) == "completed"
        # F19 修复：保留旧 envelope 全部字段（success / code / activation / review / resource_refs）
        # 让 web UI / CLI / LLM 不丢失激活信息和 resource refresh 清单
        provider_id = ""
        alias = ""
        hint = result.message or ""
        review_data: dict | None = None
        activation_data: dict | None = None
        resource_refs_list: list[str] = []
        if isinstance(result.data, dict):
            if isinstance(result.data.get("review"), dict):
                review_data = result.data["review"]
            if isinstance(result.data.get("activation"), dict):
                activation_data = result.data["activation"]
                provider_id = activation_data.get("provider_id", "")
                alias = activation_data.get("alias", "")
            if isinstance(result.data.get("resource_refs"), list):
                resource_refs_list = list(result.data["resource_refs"])
        return SetupQuickConnectResult(
            status="written" if is_success else "rejected",
            target="octoagent.yaml",
            provider_id=provider_id,
            alias=alias,
            hint=hint,
            reason=None if is_success else f"{result.code}: {result.message}",
            success=is_success,
            code=None if is_success else result.code,
            review=review_data,
            activation=activation_data,
            resource_refs=resource_refs_list,
        )

    for handler in (
        config_inspect,
        config_add_provider,
        config_set_model_alias,
        config_sync,
        setup_review,
        setup_quick_connect,
    ):
        await broker.try_register(reflect_tool_schema(handler), handler)

    # 向 ToolRegistry 注册 ToolEntry（Feature 084 T012 — entrypoints 迁移）
    for _name, _handler, _sel in (
        ("config.inspect",         config_inspect,         SideEffectLevel.NONE),
        ("config.add_provider",    config_add_provider,    SideEffectLevel.REVERSIBLE),
        ("config.set_model_alias", config_set_model_alias, SideEffectLevel.REVERSIBLE),
        ("config.sync",            config_sync,            SideEffectLevel.REVERSIBLE),
        ("setup.review",           setup_review,           SideEffectLevel.NONE),
        ("setup.quick_connect",    setup_quick_connect,    SideEffectLevel.REVERSIBLE),
    ):
        _registry_register(ToolEntry(
            name=_name,
            entrypoints=_TOOL_ENTRYPOINTS[_name],
            toolset="ops_tools",
            handler=_handler,
            schema=BaseModel,
            side_effect_level=_sel,
        ))
