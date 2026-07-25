"""F108a W3：SetupDomainService 的 config / secret IO 职责簇 mixin。

职责边界：runtime secret 写入（.env + credential store 双落盘）、config UI
hints、env 文件读写、secret audit、config 校验错误格式化、provider runtime
详情与 bridge refs 收集。新增"配置/凭证 IO"类方法放这里，防止职责堆回
setup_service.py。

依赖约定（由继承类 SetupDomainService 提供，经 MRO 解析）：
- ``self._ctx`` / ``self._stores``（DomainServiceBase）
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from octoagent.core.models import ConfigFieldHint
from octoagent.gateway.services.config.config_schema import OctoAgentConfig
from octoagent.gateway.services.operations.secret_service import SecretService
from octoagent.provider.auth.credentials import ApiKeyCredential
from octoagent.provider.auth.profile import ProviderProfile
from octoagent.provider.auth.store import CredentialStore
from pydantic import SecretStr, ValidationError

from .secret_contract import SecretMutation, apply_secret_mutations


class SetupConfigIOMixin:
    """Config / secret IO 职责簇：见模块 docstring。

    本 mixin 不持有状态，所有依赖（self._ctx / self._stores 等）由继承类
    SetupDomainService 提供。方法签名、返回值与副作用与拆分前完全等价
    （F108a 行为零变更）。
    """

    # ── config / secret ──────────────────────────────────────────

    def _save_runtime_secret_values(
        self,
        *,
        config: OctoAgentConfig,
        secret_values: Mapping[str, Any],
    ) -> dict[str, Any]:
        provider_targets, runtime_targets = self._runtime_secret_targets(config)
        target_names = provider_targets | runtime_targets
        env_path = self._ctx.project_root / ".env"
        existing = self._env_file_values(env_path)
        try:
            parsed = self._parse_secret_mutations(
                secret_values,
                target_names=target_names,
                existing=existing,
            )
            resolved = apply_secret_mutations(existing, secret_values)
        except ValueError as exc:
            raise self._action_error(
                "SECRET_MUTATION_INVALID",
                "secret_values 不符合 keep/replace/remove 合同",
            ) from exc
        if not parsed:
            return {
                "provider_env_names": [],
                "runtime_env_names": [],
                "profile_names": [],
                "removed_env_names": [],
            }

        updates = {
            name: resolved[name] for name, mutation in parsed.items() if mutation.mode == "replace"
        }
        removals = {name for name, mutation in parsed.items() if mutation.mode == "remove"}
        provider_updates = {name: updates[name] for name in updates if name in provider_targets}
        runtime_updates = {name: updates[name] for name in updates if name in runtime_targets}

        # 所有 secret 统一写 .env（F081 P3b 退役 .env.litellm 后不再分文件）
        merged_env = {**provider_updates, **runtime_updates}
        self._write_env_mutations(
            env_path,
            updates=merged_env,
            removals=removals,
        )

        saved_profiles = self._save_provider_secret_profiles(
            config=config,
            provider_updates=provider_updates,
            removals=removals,
        )

        return {
            "provider_env_names": sorted(provider_updates.keys()),
            "runtime_env_names": sorted(runtime_updates.keys()),
            "profile_names": saved_profiles,
            "removed_env_names": sorted(removals),
        }

    @staticmethod
    def _parse_secret_mutations(
        secret_values: Mapping[str, Any],
        *,
        target_names: set[str],
        existing: Mapping[str, str],
    ) -> dict[str, SecretMutation]:
        parsed: dict[str, SecretMutation] = {}
        for raw_name, raw_mutation in secret_values.items():
            name = str(raw_name).strip()
            if not name or name not in target_names:
                raise ValueError("secret name 未由当前配置声明")
            mutation = SecretMutation.model_validate(raw_mutation)
            if mutation.mode == "keep" and name not in existing:
                raise ValueError("keep 只能用于已配置 secret")
            parsed[name] = mutation
        return parsed

    @staticmethod
    def _runtime_secret_targets(config: OctoAgentConfig) -> tuple[set[str], set[str]]:
        """返回当前 config 实际声明的 provider/runtime secret env names。"""

        provider_targets = {
            env_name
            for provider in config.providers
            if (env_name := provider.effective_api_key_env)
        }
        runtime_targets = {
            env_name
            for env_name in (
                config.front_door.bearer_token_env,
                config.front_door.trusted_proxy_token_env,
                config.channels.telegram.bot_token_env,
                config.channels.telegram.webhook_secret_env,
            )
            if env_name
        }
        return provider_targets, runtime_targets

    def _save_provider_secret_profiles(
        self,
        *,
        config: OctoAgentConfig,
        provider_updates: Mapping[str, str],
        removals: set[str],
    ) -> list[str]:
        store = self._credential_store()
        saved_profiles: list[str] = []
        for provider in config.providers:
            env_name = provider.effective_api_key_env
            if provider.effective_auth_kind != "api_key" or not env_name:
                continue
            secret_value = provider_updates.get(env_name)
            if secret_value:
                profile = self._provider_secret_profile(
                    store=store,
                    provider_id=provider.id,
                    secret_value=secret_value,
                )
                store.set_profile(profile)
                saved_profiles.append(profile.name)
            if env_name in removals:
                store.remove_profile(f"{provider.id}-default")
        return saved_profiles

    @staticmethod
    def _provider_secret_profile(
        *,
        store: CredentialStore,
        provider_id: str,
        secret_value: str,
    ) -> ProviderProfile:
        profile_name = f"{provider_id}-default"
        existing = store.get_profile(profile_name)
        return ProviderProfile(
            name=profile_name,
            provider=provider_id,
            auth_mode="api_key",
            credential=ApiKeyCredential(
                provider=provider_id,
                key=SecretStr(secret_value),
            ),
            is_default=(
                existing.is_default if existing is not None else store.get_default_profile() is None
            ),
            created_at=existing.created_at if existing is not None else datetime.now(tz=UTC),
            updated_at=datetime.now(tz=UTC),
        )

    def _build_config_ui_hints(self) -> dict[str, ConfigFieldHint]:
        # Runtime 旧模型配置提示已经整体退役。
        hints = {
            "memory.reasoning_model_alias": ConfigFieldHint(
                field_path="memory.reasoning_model_alias",
                section="memory-models",
                label="加工模型别名",
                description="负责片段整理、摘要、候选结论与候选事实加工。",
                placeholder="main",
                help_text="留空时默认回退到 main。",
                order=33,
            ),
            "memory.expand_model_alias": ConfigFieldHint(
                field_path="memory.expand_model_alias",
                section="memory-models",
                label="扩写模型别名",
                description="负责 recall query expansion；不填时回退到 main。",
                placeholder="main",
                help_text="适合绑定成本较低、理解查询改写较稳定的 alias。",
                order=34,
            ),
            "memory.embedding_model_alias": ConfigFieldHint(
                field_path="memory.embedding_model_alias",
                section="memory-models",
                label="Embedding 模型别名",
                description="负责语义检索 projection。留空时走内建默认层。",
                placeholder="knowledge-embed",
                help_text="后续切换 embedding 时会触发后台重建，不会立即替换现网索引。",
                order=35,
            ),
            "memory.rerank_model_alias": ConfigFieldHint(
                field_path="memory.rerank_model_alias",
                section="memory-models",
                label="Rerank 模型别名",
                description="负责召回结果重排；不填时回退到 heuristic。",
                placeholder="memory-rerank",
                help_text="没有专门 rerank alias 也可以先留空。",
                order=36,
            ),
            "providers": ConfigFieldHint(
                field_path="providers",
                section="providers",
                label="模型提供方列表",
                description="这里配置 OpenRouter、OpenAI 等模型提供方。",
                widget="provider-list",
                placeholder="[]",
                order=40,
            ),
            "model_aliases": ConfigFieldHint(
                field_path="model_aliases",
                section="models",
                label="模型别名",
                widget="alias-map",
                placeholder="{}",
                order=50,
            ),
            "front_door.mode": ConfigFieldHint(
                field_path="front_door.mode",
                section="security",
                label="对外访问模式",
                description="控制谁可以访问 owner-facing API。",
                widget="select",
                help_text="本机使用 loopback；公网部署使用 bearer 或 trusted_proxy。",
                order=55,
            ),
            "front_door.bearer_token_env": ConfigFieldHint(
                field_path="front_door.bearer_token_env",
                section="security",
                label="Bearer Token 环境变量",
                widget="env-ref",
                sensitive=True,
                help_text="仅在 bearer 模式下需要。",
                order=56,
            ),
            "front_door.trusted_proxy_header": ConfigFieldHint(
                field_path="front_door.trusted_proxy_header",
                section="security",
                label="Trusted Proxy Header",
                help_text="trusted_proxy 模式下由反向代理注入的共享 header。",
                order=57,
            ),
            "front_door.trusted_proxy_token_env": ConfigFieldHint(
                field_path="front_door.trusted_proxy_token_env",
                section="security",
                label="Trusted Proxy Token 环境变量",
                widget="env-ref",
                sensitive=True,
                order=58,
            ),
            "front_door.trusted_proxy_cidrs": ConfigFieldHint(
                field_path="front_door.trusted_proxy_cidrs",
                section="security",
                label="Trusted Proxy 来源 CIDR",
                widget="string-list",
                help_text="必须限制为受信代理来源，避免旁路直接访问 Gateway。",
                order=59,
            ),
            "channels.telegram.enabled": ConfigFieldHint(
                field_path="channels.telegram.enabled",
                section="channels",
                label="启用 Telegram",
                widget="toggle",
                help_text="启用前需完成 Provider 和 Secret 配置。",
                order=60,
            ),
            "channels.telegram.mode": ConfigFieldHint(
                field_path="channels.telegram.mode",
                section="channels",
                label="Telegram 接入模式",
                widget="select",
                order=70,
            ),
            "channels.telegram.bot_token_env": ConfigFieldHint(
                field_path="channels.telegram.bot_token_env",
                section="channels",
                label="Telegram Bot Token 环境变量",
                widget="env-ref",
                sensitive=True,
                order=80,
            ),
            "channels.telegram.webhook_url": ConfigFieldHint(
                field_path="channels.telegram.webhook_url",
                section="channels",
                label="Webhook URL",
                help_text="仅 webhook 模式需要。无公网 HTTPS 时使用 polling。",
                order=90,
            ),
            "channels.telegram.webhook_secret_env": ConfigFieldHint(
                field_path="channels.telegram.webhook_secret_env",
                section="channels",
                label="Webhook Secret 环境变量",
                widget="env-ref",
                sensitive=True,
                order=95,
            ),
            "channels.telegram.dm_policy": ConfigFieldHint(
                field_path="channels.telegram.dm_policy",
                section="channels",
                label="私聊访问策略",
                widget="select",
                help_text="pairing 需配对后使用；open 允许任意用户触发。",
                order=97,
            ),
            "channels.telegram.allow_users": ConfigFieldHint(
                field_path="channels.telegram.allow_users",
                section="channels",
                label="允许的私聊用户",
                widget="string-list",
                order=100,
            ),
            "channels.telegram.group_policy": ConfigFieldHint(
                field_path="channels.telegram.group_policy",
                section="channels",
                label="群聊访问策略",
                widget="select",
                help_text="allowlist 限定可触发的群组；open 允许所有群组。",
                order=105,
            ),
            "channels.telegram.allowed_groups": ConfigFieldHint(
                field_path="channels.telegram.allowed_groups",
                section="channels",
                label="允许的群组",
                widget="string-list",
                order=110,
            ),
            "channels.telegram.group_allow_users": ConfigFieldHint(
                field_path="channels.telegram.group_allow_users",
                section="channels",
                label="群聊内允许用户",
                widget="string-list",
                order=115,
            ),
        }
        return hints

    # ── env / credential / runtime helpers ───────────────────────

    def _credential_store(self) -> CredentialStore:
        return CredentialStore(store_path=self._ctx.project_root / "auth-profiles.json")

    def _env_file_values(self, path: Path) -> dict[str, str]:
        if not path.exists():
            return {}
        values: dict[str, str] = {}
        for line in path.read_text(encoding="utf-8").splitlines():
            stripped = line.strip()
            if not stripped or stripped.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            key = key.strip()
            if key:
                values[key] = value
        return values

    def _write_env_values(self, path: Path, updates: Mapping[str, str]) -> None:
        self._write_env_mutations(path, updates=updates, removals=set())

    def _write_env_mutations(
        self,
        path: Path,
        *,
        updates: Mapping[str, str],
        removals: set[str],
    ) -> None:
        normalized = {
            str(key).strip(): str(value)
            for key, value in updates.items()
            if str(key).strip() and str(value).strip()
        }
        normalized_removals = {str(name).strip() for name in removals if str(name).strip()}
        if not normalized and not normalized_removals:
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
            if env_name in normalized_removals:
                continue
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

    async def _safe_secret_audit(self, project_ref: str | None) -> Any | None:
        try:
            return await SecretService(
                self._ctx.project_root,
                store_group=self._stores,
            ).audit(project_ref=project_ref)
        except Exception:
            return None

    @staticmethod
    def _format_config_validation_errors(exc: ValidationError) -> list[str]:
        messages: list[str] = []
        for item in exc.errors():
            loc = ".".join(str(part) for part in item.get("loc", ()))
            message = str(item.get("msg", "")).strip()
            if loc and message:
                messages.append(f"{loc}: {message}")
            elif message:
                messages.append(message)
        return messages or [str(exc)]

    def _collect_provider_runtime_details(
        self,
        config_value: Mapping[str, Any],
        *,
        secret_audit: Any | None,
        bridge_refs: list[dict[str, Any]],
    ) -> dict[str, Any]:
        providers = [item for item in config_value.get("providers", []) if isinstance(item, dict)]
        env_runtime = self._env_file_values(self._ctx.project_root / ".env")
        provider_env_names = {
            str(item.get("api_key_env", "")).strip()
            for item in providers
            if str(item.get("api_key_env", "")).strip() in env_runtime
        }
        profiles = self._credential_store().list_profiles()
        oauth_profile = next(
            (profile for profile in profiles if profile.provider == "openai-codex"),
            None,
        )
        return {
            "enabled_provider_ids": [
                item.get("id", "") for item in providers if item.get("enabled", True)
            ],
            "provider_entries": providers,
            "model_aliases": sorted(config_value.get("model_aliases", {}).keys()),
            "bridge_ref_count": len(bridge_refs),
            "secret_audit_status": secret_audit.overall_status if secret_audit else "unknown",
            "provider_env_names": sorted(provider_env_names),
            "runtime_env_names": sorted(env_runtime.keys()),
            "credential_profiles": [
                {
                    "name": profile.name,
                    "provider": profile.provider,
                    "auth_mode": profile.auth_mode,
                    "is_default": profile.is_default,
                    "expires_at": (
                        profile.credential.expires_at.isoformat()
                        if hasattr(profile.credential, "expires_at")
                        and getattr(profile.credential, "expires_at", None) is not None
                        else ""
                    ),
                    "account_id": (str(getattr(profile.credential, "account_id", "") or "")),
                }
                for profile in profiles
            ],
            "openai_oauth_connected": oauth_profile is not None,
            "openai_oauth_profile": oauth_profile.name if oauth_profile is not None else "",
        }

    async def _collect_bridge_refs(self) -> list[dict[str, Any]]:
        from octoagent.core.models import ProjectBindingType

        project = await self._stores.project_store.get_default_project()
        if project is None:
            return []
        bindings = await self._stores.project_store.list_bindings(project.project_id)
        results: list[dict[str, Any]] = []
        for binding in bindings:
            if binding.binding_type not in {
                ProjectBindingType.ENV_REF,
                ProjectBindingType.ENV_FILE,
            }:
                continue
            results.append(binding.model_dump(mode="json"))
        return results
