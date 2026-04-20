"""Feature 079 Phase 4 —— auth-profiles.json ↔ octoagent.yaml 漂移检测。

背景：之前"OAuth 授权成功但 provider 没入 config"的事故，核心是两套持久化
（auth-profiles.json / octoagent.yaml）缺少对账，谁也不知道对方有没有跟上。
这里提供一个纯函数 ``detect_auth_config_drift``，Gateway 启动时和诊断 API 都
可以调用，把漂移结构化暴露给用户 / 日志。

不强制修复 —— 只是警告 + 提示。真正修复路径走 Phase 2 的 setup.oauth_and_apply。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

import structlog

from octoagent.provider.auth.credentials import OAuthCredential
from octoagent.provider.auth.store import CredentialStore

from .config_wizard import load_config

log = structlog.get_logger()

DriftSeverity = Literal["high", "warning", "info"]
DriftType = Literal[
    "oauth_profile_not_in_config",
    "config_provider_no_credential",
    "alias_provider_disabled_or_missing",
    "alias_provider_unknown",
]


@dataclass
class DriftRecord:
    """单条漂移记录，用于诊断 API 响应和日志。"""

    drift_type: DriftType
    severity: DriftSeverity
    provider: str
    summary: str
    recommended_action: str
    # 额外元信息（如涉及的 profile_name / alias_name）；不含凭证值
    details: dict[str, Any] = field(default_factory=dict)

    def to_payload(self) -> dict[str, Any]:
        return {
            "drift_type": self.drift_type,
            "severity": self.severity,
            "provider": self.provider,
            "summary": self.summary,
            "recommended_action": self.recommended_action,
            "details": dict(self.details),
        }


def detect_auth_config_drift(
    project_root: Path,
    *,
    credential_store: CredentialStore | None = None,
) -> list[DriftRecord]:
    """扫描 auth-profiles ↔ octoagent.yaml 之间的不一致。

    Returns:
        漂移记录列表；空列表表示配置一致。

    规则：
    1. ``auth-profiles.json`` 里有 OAuth profile 但 ``octoagent.yaml.providers[]``
       没有对应 enabled provider → oauth_profile_not_in_config
    2. ``octoagent.yaml.providers[]`` 里有 ``auth_type=oauth`` 但
       ``auth-profiles.json`` 没对应 profile → config_provider_no_credential
    3. ``model_aliases[alias].provider`` 指向一个**disabled / 不存在**的 provider
       → alias_provider_disabled_or_missing
    """
    store = credential_store or CredentialStore()
    try:
        profiles = store.list_profiles()
    except Exception as exc:
        log.warning(
            "drift_check_profile_load_failed", error_type=type(exc).__name__
        )
        profiles = []

    try:
        config = load_config(project_root)
    except Exception as exc:
        log.warning("drift_check_config_load_failed", error_type=type(exc).__name__)
        config = None

    records: list[DriftRecord] = []

    # 规则 1 + 2：providers[] 与 auth-profiles 对账
    enabled_providers_by_id: dict[str, Any] = {}
    if config is not None:
        for provider_entry in config.providers:
            if provider_entry.enabled:
                enabled_providers_by_id[provider_entry.id] = provider_entry

    oauth_profile_providers: dict[str, str] = {}
    for profile in profiles:
        if profile.auth_mode != "oauth":
            continue
        if not isinstance(profile.credential, OAuthCredential):
            continue
        oauth_profile_providers[profile.provider] = profile.name

    for provider_id, profile_name in oauth_profile_providers.items():
        if provider_id not in enabled_providers_by_id:
            records.append(
                DriftRecord(
                    drift_type="oauth_profile_not_in_config",
                    severity="high",
                    provider=provider_id,
                    summary=(
                        f"auth-profiles.json 里已有 {provider_id} 的 OAuth 凭证，"
                        f"但 octoagent.yaml 的 providers[] 没启用这个 provider。"
                        f"主 Agent 不会把该 provider 纳入模型路由。"
                    ),
                    recommended_action=(
                        "在 Settings 页面添加对应 provider 条目并保存；"
                        "或者用 setup.oauth_and_apply 一次完成授权 + 入 config。"
                    ),
                    details={"profile_name": profile_name},
                )
            )

    if config is not None:
        for provider_entry in config.providers:
            if not provider_entry.enabled or provider_entry.auth_type != "oauth":
                continue
            if provider_entry.id not in oauth_profile_providers:
                records.append(
                    DriftRecord(
                        drift_type="config_provider_no_credential",
                        severity="high",
                        provider=provider_entry.id,
                        summary=(
                            f"octoagent.yaml 声明了 OAuth provider {provider_entry.id}，"
                            f"但 auth-profiles.json 没对应凭证。调用该 provider 会因为"
                            f"缺 token 而失败。"
                        ),
                        recommended_action=(
                            "在 Settings 页面走一次 OAuth 授权，或者删除该 provider"
                            "条目以免主 Agent 误路由过去。"
                        ),
                    )
                )

    # 规则 3：alias provider 必须在 enabled 列表中
    if config is not None:
        for alias_name, alias_def in config.model_aliases.items():
            if alias_def.provider in enabled_providers_by_id:
                continue
            # provider 在 config 里但未启用 → disabled
            known_but_disabled = any(
                p.id == alias_def.provider for p in config.providers
            )
            records.append(
                DriftRecord(
                    drift_type="alias_provider_disabled_or_missing"
                    if known_but_disabled
                    else "alias_provider_unknown",
                    severity="high",
                    provider=alias_def.provider,
                    summary=(
                        f"model alias {alias_name!r} 指向 provider "
                        f"{alias_def.provider!r}，但该 provider "
                        f"{'已禁用' if known_but_disabled else '不在 providers[] 列表里'}。"
                    ),
                    recommended_action=(
                        f"把 {alias_name} 指向一个已启用的 provider，"
                        f"或在 Settings 页面启用 {alias_def.provider}。"
                    ),
                    details={"alias": alias_name, "model": alias_def.model},
                )
            )

    return records
