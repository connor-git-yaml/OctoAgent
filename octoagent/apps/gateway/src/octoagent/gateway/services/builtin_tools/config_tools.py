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

from octoagent.tooling import SideEffectLevel, reflect_tool_schema, tool_contract

from ._deps import ToolDeps


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
    from octoagent.provider.dx.config_wizard import load_config as _load_config
    from octoagent.provider.dx.config_wizard import save_config as _save_config

    @tool_contract(
        name="config.inspect",
        side_effect_level=SideEffectLevel.NONE,
        tool_group="config",
        tags=["config", "provider", "model", "inspect"],
        manifest_ref="builtin://config.inspect",
        metadata={
            "entrypoints": ["agent_runtime", "web"],
            "runtime_kinds": ["worker", "subagent", "graph_agent"],
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
        metadata={
            "entrypoints": ["agent_runtime", "web"],
            "runtime_kinds": ["worker", "subagent", "graph_agent"],
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
    ) -> str:
        """添加或更新一个 LLM Provider 到 octoagent.yaml。修改后可执行 config.sync 重新生成 LiteLLM 衍生配置。"""
        config = _load_config(deps.project_root)
        if config is None:
            return json.dumps(
                {"error": "CONFIG_NOT_FOUND", "message": "octoagent.yaml 不存在"},
                ensure_ascii=False,
            )
        provider_id = provider_id.strip().lower()
        if not provider_id:
            return json.dumps(
                {"error": "MISSING_PARAM", "message": "provider_id 不能为空"},
                ensure_ascii=False,
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

        from octoagent.provider.dx.config_schema import ProviderEntry
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
        return json.dumps(
            {
                "success": True,
                "action": action,
                "provider_id": provider_id,
                "hint": (
                    "执行 config.sync 重新生成 litellm-config.yaml；"
                    "如需保存并启用真实模型，可继续执行 setup.quick_connect；"
                    "也可使用 Web 设置页或 CLI `octo setup`。"
                ),
            },
            ensure_ascii=False,
        )

    @tool_contract(
        name="config.set_model_alias",
        side_effect_level=SideEffectLevel.REVERSIBLE,
        tool_group="config",
        tags=["config", "model", "alias", "set"],
        manifest_ref="builtin://config.set_model_alias",
        metadata={
            "entrypoints": ["agent_runtime", "web"],
            "runtime_kinds": ["worker", "subagent", "graph_agent"],
        },
    )
    async def config_set_model_alias(
        alias: str,
        provider: str,
        model: str,
        description: str = "",
        thinking_level: str = "",
    ) -> str:
        """设置模型别名（如 main, cheap）到具体 Provider + 模型。修改后可执行 config.sync 重新生成 LiteLLM 衍生配置。"""
        config = _load_config(deps.project_root)
        if config is None:
            return json.dumps(
                {"error": "CONFIG_NOT_FOUND", "message": "octoagent.yaml 不存在"},
                ensure_ascii=False,
            )
        alias = alias.strip().lower()
        if not alias or not provider.strip() or not model.strip():
            return json.dumps(
                {"error": "MISSING_PARAM", "message": "alias, provider, model 都不能为空"},
                ensure_ascii=False,
            )
        # 校验 provider 存在
        provider_ids = {p.id for p in config.providers}
        if provider.strip() not in provider_ids:
            return json.dumps(
                {"error": "UNKNOWN_PROVIDER", "message": f"Provider '{provider}' 不存在", "available": sorted(provider_ids)},
                ensure_ascii=False,
            )
        from octoagent.provider.dx.config_schema import ModelAlias as _ModelAlias
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
        return json.dumps(
            {
                "success": True,
                "alias": alias,
                "provider": provider.strip(),
                "model": model.strip(),
                "hint": (
                    "执行 config.sync 重新生成 litellm-config.yaml；"
                    "如需保存并启用真实模型，可继续执行 setup.quick_connect；"
                    "也可使用 Web 设置页或 CLI `octo setup`。"
                ),
            },
            ensure_ascii=False,
        )

    @tool_contract(
        name="config.sync",
        side_effect_level=SideEffectLevel.REVERSIBLE,
        tool_group="config",
        tags=["config", "sync", "litellm"],
        manifest_ref="builtin://config.sync",
        metadata={
            "entrypoints": ["agent_runtime", "web"],
            "runtime_kinds": ["worker", "subagent", "graph_agent"],
        },
    )
    async def config_sync() -> str:
        """把 octoagent.yaml 重新生成到 litellm-config.yaml。等同于 `octo config sync`，但不会自动重启 runtime。"""
        try:
            config = _load_config(deps.project_root)
            if config is None:
                return json.dumps(
                    {"error": "CONFIG_NOT_FOUND", "message": "octoagent.yaml 不存在"},
                    ensure_ascii=False,
                )
            from octoagent.provider.dx.litellm_generator import generate_litellm_config as _gen_litellm
            out_path = _gen_litellm(config, deps.project_root)
            enabled_providers = [p.id for p in config.providers if p.enabled]
            enabled_aliases = [
                k for k, v in config.model_aliases.items()
                if any(p.id == v.provider and p.enabled for p in config.providers)
            ]
            return json.dumps(
                {
                    "success": True,
                    "message": "LiteLLM 衍生配置已同步",
                    "output_path": str(out_path),
                    "enabled_providers": enabled_providers,
                    "enabled_aliases": enabled_aliases,
                    "hint": (
                        "这一步只会重新生成 litellm-config.yaml；"
                        "如需启动或切换到真实模型，请使用 Web 设置页的\u201c连接真实模型\u201d"
                        "或 CLI `octo setup`。"
                    ),
                },
                ensure_ascii=False,
            )
        except Exception as exc:
            return json.dumps(
                {"error": "SYNC_FAILED", "message": str(exc)},
                ensure_ascii=False,
            )

    @tool_contract(
        name="setup.review",
        side_effect_level=SideEffectLevel.NONE,
        tool_group="setup",
        tags=["setup", "review", "config"],
        manifest_ref="builtin://setup.review",
        metadata={
            "entrypoints": ["agent_runtime", "web"],
            "runtime_kinds": ["worker", "subagent", "graph_agent"],
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
        metadata={
            "entrypoints": ["agent_runtime", "web"],
            "runtime_kinds": ["worker", "subagent", "graph_agent"],
        },
    )
    async def setup_quick_connect(draft_json: str) -> str:
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
            return json.dumps(
                {"error": "INVALID_DRAFT_JSON", "message": str(exc)},
                ensure_ascii=False,
            )

        from octoagent.provider.dx.setup_governance_adapter import (
            LocalSetupGovernanceAdapter,
        )

        adapter = LocalSetupGovernanceAdapter(deps.project_root)
        prepared = await adapter.prepare_wizard_draft(draft)
        result = await adapter.quick_connect(prepared)
        payload: dict[str, Any] = {
            "success": str(result.status) == "completed",
            "status": str(result.status),
            "code": result.code,
            "message": result.message,
        }
        if isinstance(result.data, dict):
            if isinstance(result.data.get("review"), dict):
                payload["review"] = result.data["review"]
            if isinstance(result.data.get("activation"), dict):
                payload["activation"] = result.data["activation"]
            if result.data.get("resource_refs"):
                payload["resource_refs"] = result.data["resource_refs"]
        if not payload["success"]:
            payload["error"] = result.code
        return json.dumps(payload, ensure_ascii=False)

    for handler in (
        config_inspect,
        config_add_provider,
        config_set_model_alias,
        config_sync,
        setup_review,
        setup_quick_connect,
    ):
        await broker.try_register(reflect_tool_schema(handler), handler)
