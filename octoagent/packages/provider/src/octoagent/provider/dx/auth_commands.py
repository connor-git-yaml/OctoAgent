"""Auth CLI 命令 -- Feature 064: paste-token 子命令

对齐 contracts/claude-provider-api.md SS1, FR-008, FR-010。
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import click
from pydantic import SecretStr

from ..auth.credentials import OAuthCredential
from ..auth.profile import ProviderProfile
from ..auth.store import CredentialStore
from ..auth.validators import validate_claude_setup_token

# Claude access_token 默认有效期（8 小时）
_CLAUDE_TOKEN_TTL_SECONDS = 28800


@click.group()
def auth() -> None:
    """认证管理命令"""


@auth.command("paste-token")
@click.option(
    "--provider",
    type=click.Choice(["anthropic-claude"]),
    default="anthropic-claude",
    help="目标 Provider（目前仅支持 anthropic-claude）",
)
def paste_token(provider: str) -> None:
    """通过粘贴 setup-token 导入 Claude 订阅凭证。

    使用 Claude Code CLI 生成的 setup-token（access_token + refresh_token）
    导入 Claude 订阅凭证。导入后系统将自动管理 token 刷新。

    对齐 contracts/claude-provider-api.md SS1。
    """
    click.echo()
    click.echo("Claude 订阅凭证导入")
    click.echo("=" * 20)
    click.echo()
    click.echo(
        "注意: 此功能使用 Claude Code CLI 的 setup-token 机制，\n"
        "属于\"技术兼容性\"范畴，非 Anthropic 官方支持的用法。\n"
        "Anthropic 可能在未来限制此类使用。建议同时配置 API Key 作为备选。"
    )
    click.echo()
    click.echo("请按以下步骤操作:")
    click.echo("1. 确保已安装 Claude Code CLI")
    click.echo("2. 运行: claude setup-token")
    click.echo("3. 将输出的 token 粘贴到下方")
    click.echo()

    # 接收 access_token
    access_token = click.prompt(
        "粘贴 access_token (sk-ant-oat01-...)",
        hide_input=True,
    )

    # 接收 refresh_token
    refresh_token = click.prompt(
        "粘贴 refresh_token (sk-ant-ort01-...)",
        hide_input=True,
    )

    # 校验
    click.echo()
    click.echo("验证中...")
    is_valid, error_msg = validate_claude_setup_token(access_token, refresh_token)
    if not is_valid:
        raise click.ClickException(f"凭证校验失败: {error_msg}")

    # 存储为 OAuthCredential
    store = CredentialStore()
    profile_name = f"{provider}-default"

    credential = OAuthCredential(
        provider=provider,
        access_token=SecretStr(access_token),
        refresh_token=SecretStr(refresh_token),
        expires_at=datetime.now(tz=UTC) + timedelta(seconds=_CLAUDE_TOKEN_TTL_SECONDS),
        account_id=None,  # Claude token 不是 JWT，无法提取 account_id
    )

    profile = ProviderProfile(
        name=profile_name,
        provider=provider,
        auth_mode="oauth",
        credential=credential,
        is_default=False,  # 不自动设为默认 Provider
        created_at=datetime.now(tz=UTC),
        updated_at=datetime.now(tz=UTC),
    )

    store.set_profile(profile)

    click.echo()
    click.echo("凭证已保存为 Claude (Subscription) profile。")
    click.echo("access_token 有效期约 8 小时，系统将自动刷新。")
    click.echo()
