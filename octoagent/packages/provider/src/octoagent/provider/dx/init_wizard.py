"""octo init 交互式引导 -- 对齐 contracts/dx-cli-api.md SS2, FR-007

完整流程:
1. 检测运行模式（echo/litellm）
2. Provider 选择
3. 认证模式选择
4. 凭证输入/获取 + 格式校验
5. 存入 credential store
6. Master Key 生成
7. Docker 检测
8. 配置文件生成
9. 输出摘要
"""

from __future__ import annotations

import asyncio
import os
import secrets
import subprocess
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import questionary
import structlog
from pydantic import SecretStr

from ..auth.credentials import (
    ApiKeyCredential,
    Credential,
    OAuthCredential,
    TokenCredential,
)
from ..auth.environment import detect_environment
from ..auth.oauth import DeviceFlowConfig, poll_for_token, start_device_flow
from ..auth.oauth_flows import run_auth_code_pkce_flow
from ..auth.oauth_provider import (
    DISPLAY_TO_CANONICAL,
    OAuthProviderRegistry,
)
from ..auth.profile import ProviderProfile
from ..auth.store import CredentialStore
from ..auth.validators import validate_api_key, validate_setup_token
from .console_output import create_console, render_panel
from .models import InitConfig

log = structlog.get_logger()
console = create_console()

# Provider 选项配置
PROVIDERS: dict[str, dict[str, Any]] = {
    "openrouter": {
        "name": "OpenRouter",
        "auth_modes": ["api_key"],
        "env_key": "OPENROUTER_API_KEY",
    },
    "openai": {
        "name": "OpenAI",
        "auth_modes": ["api_key", "oauth"],
        "env_key": "OPENAI_API_KEY",
    },
    "github": {
        "name": "GitHub Copilot",
        "auth_modes": ["oauth"],
        "env_key": "GITHUB_TOKEN",
    },
    "anthropic": {
        "name": "Anthropic",
        "auth_modes": ["api_key", "token"],
        "env_key": "ANTHROPIC_API_KEY",
    },
}

# 认证模式显示名称
AUTH_MODE_LABELS: dict[str, str] = {
    "api_key": "API Key（标准密钥）",
    "token": "Setup Token（免费试用）",
    "oauth": "OAuth PKCE（免费试用，浏览器授权）",
}

# Setup Token 默认 TTL（小时）
_DEFAULT_SETUP_TOKEN_TTL_HOURS = 24


def detect_partial_init(project_root: Path) -> bool:
    """检测是否存在半成品配置 (EC-3)

    检查 .env 是否存在但 .env.litellm 不存在等不一致状态。
    """
    env_exists = (project_root / ".env").exists()
    env_litellm_exists = (project_root / ".env.litellm").exists()
    litellm_config_exists = (project_root / "litellm-config.yaml").exists()

    # 有部分文件存在但不完整
    files = [env_exists, env_litellm_exists, litellm_config_exists]
    return any(files) and not all(files)


def _prompt_overwrite() -> bool:
    """提示用户是否覆盖已有配置"""
    return questionary.confirm(
        "检测到已有配置文件，是否覆盖？",
        default=False,
    ).ask() or False


def _select_llm_mode() -> str:
    """选择运行模式"""
    return questionary.select(
        "选择 LLM 运行模式:",
        choices=[
            questionary.Choice("litellm - 通过 LiteLLM Proxy 调用真实 LLM", value="litellm"),
            questionary.Choice("echo - 开发测试模式（回显输入）", value="echo"),
        ],
    ).ask() or "echo"


def _select_provider() -> str:
    """选择 Provider"""
    choices = [
        questionary.Choice(_build_provider_choice_label(key, info), value=key)
        for key, info in PROVIDERS.items()
    ]
    return questionary.select(
        "选择 LLM Provider:",
        choices=choices,
    ).ask() or "openrouter"


def _select_auth_mode(provider: str) -> str:
    """根据 Provider 列出可用认证模式"""
    provider_info = PROVIDERS.get(provider, {"auth_modes": ["api_key"]})
    modes = provider_info["auth_modes"]

    if len(modes) == 1:
        return modes[0]

    choices: list[questionary.Choice] = []
    for mode in modes:
        if mode == "oauth":
            label = _resolve_oauth_mode_label(provider)
        else:
            label = AUTH_MODE_LABELS.get(mode, mode)
        choices.append(questionary.Choice(label, value=mode))

    return questionary.select(
        "选择认证模式:",
        choices=choices,
    ).ask() or modes[0]


def _resolve_oauth_mode_label(provider: str) -> str:
    """根据 provider 的 flow_type 生成 OAuth 模式标签"""
    canonical_id = DISPLAY_TO_CANONICAL.get(provider, provider)
    config = OAuthProviderRegistry().get(canonical_id)
    if config is None:
        return AUTH_MODE_LABELS["oauth"]

    flow_labels = {
        "auth_code_pkce": "OAuth PKCE（免费试用，浏览器授权）",
        "device_flow": "OAuth Device Flow（设备码授权）",
        "device_flow_pkce": "OAuth Device Flow + PKCE（设备码授权）",
    }
    return flow_labels.get(config.flow_type, AUTH_MODE_LABELS["oauth"])


def _build_provider_choice_label(provider: str, info: dict[str, Any]) -> str:
    """构建 Provider 选择项标签（附带 OAuth 流程类型）"""
    name = str(info.get("name", provider))
    oauth_modes = info.get("auth_modes", [])
    if "oauth" not in oauth_modes:
        return name

    oauth_label = _resolve_oauth_mode_label(provider)
    short = oauth_label.split("（", 1)[0]
    return f"{name} ({short})"


def _input_api_key(provider: str) -> ApiKeyCredential:
    """输入 API Key + 格式校验"""
    while True:
        key = questionary.password(
            f"输入 {PROVIDERS.get(provider, {}).get('name', provider)} API Key:",
        ).ask() or ""

        if validate_api_key(key, provider):
            return ApiKeyCredential(
                provider=provider,
                key=SecretStr(key),
            )
        console.print("[red]API Key 格式无效，请重新输入。[/red]")


def _input_setup_token() -> TokenCredential:
    """输入 Setup Token + 格式校验"""
    ttl_hours = int(
        os.environ.get(
            "OCTOAGENT_SETUP_TOKEN_TTL_HOURS",
            str(_DEFAULT_SETUP_TOKEN_TTL_HOURS),
        ),
    )
    while True:
        token = questionary.password(
            "输入 Anthropic Setup Token (sk-ant-oat01-...):",
        ).ask() or ""

        if validate_setup_token(token):
            now = datetime.now(tz=UTC)
            return TokenCredential(
                provider="anthropic",
                token=SecretStr(token),
                acquired_at=now,
                expires_at=now + timedelta(hours=ttl_hours),
            )
        console.print(
            "[red]Setup Token 格式无效，必须以 sk-ant-oat01- 开头。[/red]",
        )


async def _run_oauth_pkce_flow(
    provider: str,
    force_manual: bool = False,
) -> OAuthCredential | None:
    """执行 OAuth PKCE 流程

    根据 provider display_id 从注册表获取 canonical_id，
    然后调用 run_auth_code_pkce_flow() 完成授权。

    Args:
        provider: Provider display_id（如 "openai"）
        force_manual: 是否强制手动模式

    Returns:
        OAuthCredential 实例，失败返回 None
    """
    # 解析 canonical_id
    canonical_id = DISPLAY_TO_CANONICAL.get(provider, provider)
    registry = OAuthProviderRegistry()
    config = registry.get(canonical_id)
    if config is None:
        console.print(f"[red]未找到 Provider 配置: {canonical_id}[/red]")
        return None

    # 检测环境
    env = detect_environment(force_manual=force_manual)

    try:
        console.print("[dim]正在发起 OAuth PKCE 授权...[/dim]")

        def _on_status(msg: str) -> None:
            console.print(f"[dim]{msg}[/dim]")

        credential = await run_auth_code_pkce_flow(
            config=config,
            registry=registry,
            env=env,
            on_status=_on_status,
        )
        console.print("[green]OAuth 授权成功![/green]")
        return credential
    except Exception as exc:
        console.print(f"[red]OAuth PKCE 授权失败: {exc}[/red]")
        console.print("[yellow]建议切换到 API Key 模式。[/yellow]")
        return None


async def _run_oauth_device_flow() -> OAuthCredential | None:
    """执行 Codex OAuth Device Flow 授权

    触发 Device Flow -> 显示 user_code + verification_uri -> 打开浏览器 -> 轮询等待
    """
    import webbrowser

    # Codex OAuth 默认 client_id（可通过环境变量覆盖）
    client_id = os.environ.get(
        "OCTOAGENT_CODEX_CLIENT_ID",
        "Shx2gOHmfLUQ3GbEkVU1Rwy9BooSEoPC",
    )

    config = DeviceFlowConfig(client_id=client_id)

    try:
        console.print("[dim]正在发起 Device Flow 授权...[/dim]")
        auth_resp = await start_device_flow(config)

        console.print()
        console.print(f"  验证码: [bold]{auth_resp.user_code}[/bold]")
        console.print(f"  授权地址: {auth_resp.verification_uri}")
        console.print()

        # 尝试自动打开浏览器
        if auth_resp.verification_uri_complete:
            webbrowser.open(auth_resp.verification_uri_complete)
        else:
            webbrowser.open(auth_resp.verification_uri)

        console.print("[dim]请在浏览器中完成授权，等待中...[/dim]")

        credential = await poll_for_token(
            config=config,
            device_code=auth_resp.device_code,
            interval=auth_resp.interval,
        )
        console.print("[green]OAuth 授权成功![/green]")
        return credential

    except Exception as exc:
        console.print(f"[red]OAuth 授权失败: {exc}[/red]")
        console.print("[yellow]建议切换到 API Key 模式。[/yellow]")
        return None


def _generate_master_key() -> str:
    """生成随机 LITELLM_MASTER_KEY（32 字节 hex）"""
    return f"sk-{secrets.token_hex(32)}"


def _check_docker() -> bool:
    """检测 Docker 可用性"""
    try:
        result = subprocess.run(
            ["docker", "info"],
            capture_output=True,
            timeout=10,
        )
        return result.returncode == 0
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return False


def generate_env_file(config: InitConfig, project_root: Path) -> Path:
    """生成 .env 文件

    Args:
        config: init 配置结果
        project_root: 项目根目录

    Returns:
        生成的 .env 文件路径
    """
    env_path = project_root / ".env"
    lines = [
        "# OctoAgent 环境配置（由 octo init 自动生成）",
        f"OCTOAGENT_LLM_MODE={config.llm_mode}",
    ]

    if config.llm_mode == "litellm" and config.master_key:
        lines.append(f"LITELLM_PROXY_KEY={config.master_key}")
        lines.append("LITELLM_PROXY_URL=http://localhost:4000")

    lines.append("")  # 末尾空行
    env_path.write_text("\n".join(lines), encoding="utf-8")
    return env_path


def generate_env_litellm_file(config: InitConfig, project_root: Path) -> Path:
    """生成 .env.litellm 文件"""
    env_path = project_root / ".env.litellm"
    lines = [
        "# LiteLLM Proxy 环境配置（由 octo init 自动生成）",
    ]

    if config.master_key:
        lines.append(f"LITELLM_MASTER_KEY={config.master_key}")

    # 根据 Provider 设置对应的环境变量
    if config.credential is not None:
        provider_info = PROVIDERS.get(config.provider, {})
        env_key = provider_info.get("env_key", "")
        if env_key and hasattr(config.credential, "key"):
            lines.append(
                f"{env_key}={config.credential.key.get_secret_value()}",  # type: ignore[union-attr]
            )
        elif env_key and hasattr(config.credential, "token"):
            lines.append(
                f"{env_key}={config.credential.token.get_secret_value()}",  # type: ignore[union-attr]
            )
        elif env_key and hasattr(config.credential, "access_token"):
            lines.append(
                f"{env_key}={config.credential.access_token.get_secret_value()}",  # type: ignore[union-attr]
            )

    lines.append("")  # 末尾空行
    env_path.write_text("\n".join(lines), encoding="utf-8")
    return env_path


def generate_litellm_config(config: InitConfig, project_root: Path) -> Path:
    """生成 litellm-config.yaml 文件"""
    config_path = project_root / "litellm-config.yaml"

    # 根据 Provider 生成基本模型配置
    provider = config.provider or "openrouter"
    provider_info = PROVIDERS.get(provider, {})
    env_key = provider_info.get("env_key", "OPENROUTER_API_KEY")

    yaml_content = f"""# LiteLLM Proxy 配置（由 octo init 自动生成）
model_list:
  - model_name: "main"
    litellm_params:
      model: "{_get_default_model(provider)}"
      api_key: "os.environ/{env_key}"

  - model_name: "cheap"
    litellm_params:
      model: "{_get_cheap_model(provider)}"
      api_key: "os.environ/{env_key}"

general_settings:
  master_key: "os.environ/LITELLM_MASTER_KEY"
"""
    config_path.write_text(yaml_content, encoding="utf-8")
    return config_path


def _get_default_model(provider: str) -> str:
    """获取 Provider 默认模型"""
    defaults = {
        "openrouter": "openrouter/auto",
        "openai": "gpt-4o",
        "github": "github/gpt-4.1-mini",
        "anthropic": "claude-sonnet-4-20250514",
    }
    return defaults.get(provider, "openrouter/auto")


def _get_cheap_model(provider: str) -> str:
    """获取 Provider 低成本模型（用于 doctor --live ping）"""
    defaults = {
        "openrouter": "openrouter/auto",
        "openai": "gpt-4o-mini",
        "github": "github/gpt-4.1-nano",
        "anthropic": "claude-haiku-4-20250414",
    }
    return defaults.get(provider, "openrouter/auto")


def run_init_wizard(
    project_root: Path | None = None,
    store: CredentialStore | None = None,
    manual_oauth: bool = False,
) -> InitConfig:
    """执行交互式引导配置

    同步函数 — questionary（prompt_toolkit）不兼容嵌套 asyncio event loop。
    仅 OAuth Device Flow 部分通过 asyncio.run() 独立运行。

    Args:
        project_root: 项目根目录（默认当前目录）
        store: Credential Store 实例（默认使用默认路径）

    Returns:
        InitConfig 实例
    """
    if project_root is None:
        project_root = Path.cwd()
    if store is None:
        store = CredentialStore()

    console.print(
        render_panel(
            "OctoAgent 初始化向导",
            ["即将开始交互式初始化。"],
            border_style="blue",
        )
    )

    # 检测已有配置
    has_existing = (project_root / ".env").exists()
    has_partial = detect_partial_init(project_root)

    if has_partial:
        console.print("[yellow]检测到不完整的配置文件。[/yellow]")
    if has_existing and not _prompt_overwrite():
        console.print("[dim]已取消。[/dim]")
        # 返回默认 echo 配置
        return InitConfig(llm_mode="echo")

    # 步骤 1: 选择运行模式
    llm_mode = _select_llm_mode()

    credential: Credential | None = None
    provider = ""
    auth_mode = "api_key"
    profile_provider = ""

    if llm_mode == "litellm":
        # 步骤 2: 选择 Provider
        provider = _select_provider()
        profile_provider = provider

        # 步骤 3: 选择认证模式
        auth_mode = _select_auth_mode(provider)

        # 步骤 4: 凭证输入/获取
        if auth_mode == "api_key":
            credential = _input_api_key(provider)
        elif auth_mode == "token":
            credential = _input_setup_token()
        elif auth_mode == "oauth":
            # 003-b: 根据 Provider 的 flow_type 选择 PKCE 或 Device Flow
            canonical_id = DISPLAY_TO_CANONICAL.get(provider, provider)
            profile_provider = canonical_id
            _registry = OAuthProviderRegistry()
            provider_config = _registry.get(canonical_id)
            if provider_config and provider_config.flow_type == "auth_code_pkce":
                credential = asyncio.run(
                    _run_oauth_pkce_flow(provider, force_manual=manual_oauth)
                )
            else:
                credential = asyncio.run(_run_oauth_device_flow())

        # 步骤 5: 存入 credential store
        if credential is not None:
            now = datetime.now(tz=UTC)
            profile_name = f"{profile_provider}-default"
            profile = ProviderProfile(
                name=profile_name,
                provider=profile_provider,
                auth_mode=auth_mode,  # type: ignore[arg-type]
                credential=credential,
                is_default=True,
                created_at=now,
                updated_at=now,
            )
            store.set_profile(profile)
            console.print(f"[green]凭证已保存到 profile: {profile_name}[/green]")

    # 步骤 6: Master Key 生成
    master_key = _generate_master_key() if llm_mode == "litellm" else ""

    # 步骤 7: Docker 检测
    docker_available = _check_docker()
    if docker_available:
        console.print("[green]Docker 已就绪。[/green]")
    else:
        console.print("[yellow]Docker 未检测到，LiteLLM Proxy 需要 Docker 运行。[/yellow]")

    # 构建配置
    config = InitConfig(
        llm_mode=llm_mode,  # type: ignore[arg-type]
        provider=provider,
        auth_mode=auth_mode,  # type: ignore[arg-type]
        credential=credential,
        master_key=master_key,
        docker_available=docker_available,
    )

    # 步骤 8: 生成配置文件
    generated_files: list[str] = []
    env_file = generate_env_file(config, project_root)
    generated_files.append(str(env_file))

    if llm_mode == "litellm":
        env_litellm = generate_env_litellm_file(config, project_root)
        generated_files.append(str(env_litellm))
        litellm_config = generate_litellm_config(config, project_root)
        generated_files.append(str(litellm_config))

    # 步骤 9: 输出摘要
    console.print()
    console.print(render_panel("配置完成", ["初始化流程已完成。"], border_style="green"))
    console.print(f"  运行模式: {llm_mode}")
    if provider:
        console.print(f"  Provider: {provider}")
        console.print(f"  认证模式: {auth_mode}")
    console.print(f"  生成文件: {', '.join(generated_files)}")
    console.print()
    if llm_mode == "litellm":
        console.print("[dim]下一步: docker compose -f docker-compose.litellm.yml up -d[/dim]")

    return config
