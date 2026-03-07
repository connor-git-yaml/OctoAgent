"""Telegram channel verifier。"""

from __future__ import annotations

import os
from collections.abc import Callable, Mapping
from pathlib import Path

import aiosqlite
from octoagent.core.config import get_db_path

from .channel_verifier import (
    ChannelStepResult,
    ChannelVerifierRegistry,
    VerifierAvailability,
)
from .config_schema import ConfigParseError, TelegramChannelConfig
from .config_wizard import load_config
from .onboarding_models import NextAction, OnboardingStepStatus
from .telegram_client import (
    TelegramBotApiError,
    TelegramBotClient,
    TelegramBotClientConfigError,
)
from .telegram_pairing import TelegramStateStore

TelegramBotClientFactory = Callable[[Path], TelegramBotClient]
TelegramStateStoreFactory = Callable[[Path], TelegramStateStore]


def _command_action(
    action_id: str,
    title: str,
    description: str,
    command: str,
    *,
    blocking: bool,
    sort_order: int,
) -> NextAction:
    return NextAction(
        action_id=action_id,
        action_type="command",
        title=title,
        description=description,
        command=command,
        blocking=blocking,
        sort_order=sort_order,
    )


def _manual_action(
    action_id: str,
    title: str,
    description: str,
    *,
    blocking: bool,
    sort_order: int,
    manual_steps: list[str],
) -> NextAction:
    return NextAction(
        action_id=action_id,
        action_type="manual",
        title=title,
        description=description,
        manual_steps=manual_steps,
        blocking=blocking,
        sort_order=sort_order,
    )


class TelegramOnboardingVerifier:
    """真实 Telegram verifier。"""

    channel_id = "telegram"
    display_name = "Telegram"

    def __init__(
        self,
        *,
        environ: Mapping[str, str] | None = None,
        client_factory: TelegramBotClientFactory | None = None,
        state_store_factory: TelegramStateStoreFactory | None = None,
    ) -> None:
        self._environ = environ if environ is not None else os.environ
        self._client_factory = client_factory or (
            lambda project_root: TelegramBotClient(project_root, environ=self._environ)
        )
        self._state_store_factory = state_store_factory or TelegramStateStore

    def availability(self, project_root: Path) -> VerifierAvailability:
        config, error = self._load_telegram_config(project_root)
        if error is not None:
            return error
        assert config is not None

        actions: list[NextAction] = []
        if not config.enabled:
            actions.append(
                _manual_action(
                    "enable-telegram-channel",
                    "启用 Telegram channel",
                    "当前 channels.telegram.enabled=false。",
                    blocking=True,
                    sort_order=10,
                    manual_steps=[
                        "编辑 octoagent.yaml",
                        "设置 channels.telegram.enabled=true",
                        "保存后重新运行: octo onboard --channel telegram",
                    ],
                )
            )
        if config.mode == "webhook" and not config.webhook_url:
            actions.append(
                _manual_action(
                    "complete-telegram-webhook",
                    "补齐 Telegram webhook_url",
                    "webhook 模式必须声明 webhook_url。",
                    blocking=True,
                    sort_order=20,
                    manual_steps=[
                        "编辑 octoagent.yaml",
                        "为 channels.telegram.webhook_url 设置可访问的 HTTPS 地址",
                        "保存后重新运行: octo onboard --channel telegram",
                    ],
                )
            )

        if actions:
            return VerifierAvailability(
                available=False,
                reason="Telegram channel 配置不完整。",
                actions=actions,
            )
        return VerifierAvailability(available=True)

    async def run_readiness(self, project_root: Path, session: object) -> ChannelStepResult:
        del session
        availability = self.availability(project_root)
        if not availability.available:
            return ChannelStepResult(
                channel_id=self.channel_id,
                step="channel_readiness",
                status=OnboardingStepStatus.BLOCKED,
                summary=availability.reason or "Telegram channel 不可用。",
                actions=availability.actions,
            )

        store = self._state_store_factory(project_root)
        state = store.load()
        if store.last_issue == "corrupted":
            backup_path = store.path.with_suffix(store.path.suffix + ".corrupted")
            return ChannelStepResult(
                channel_id=self.channel_id,
                step="channel_readiness",
                status=OnboardingStepStatus.ACTION_REQUIRED,
                summary="telegram-state.json 已损坏，已回退为空状态。",
                actions=[
                    _manual_action(
                        "inspect-telegram-state",
                        "检查 telegram-state.json",
                        "Telegram pairing state 无法解析。",
                        blocking=True,
                        sort_order=30,
                        manual_steps=[
                            f"检查备份文件: {backup_path}",
                            "修复或删除损坏文件后重新运行 onboarding",
                        ],
                    )
                ],
            )

        try:
            identity = await self._client_factory(project_root).get_me()
        except TelegramBotClientConfigError as exc:
            return ChannelStepResult(
                channel_id=self.channel_id,
                step="channel_readiness",
                status=OnboardingStepStatus.ACTION_REQUIRED,
                summary=str(exc),
                actions=[
                    _manual_action(
                        "set-telegram-bot-token",
                        "设置 Telegram bot token",
                        str(exc),
                        blocking=True,
                        sort_order=40,
                        manual_steps=[
                            "确认 .env / shell 中已导出 bot token 环境变量",
                            "重新运行: octo onboard --channel telegram",
                        ],
                    )
                ],
            )
        except TelegramBotApiError as exc:
            return ChannelStepResult(
                channel_id=self.channel_id,
                step="channel_readiness",
                status=OnboardingStepStatus.BLOCKED,
                summary=f"Telegram Bot API 连通性检查失败: {exc}",
                actions=[
                    _manual_action(
                        "repair-telegram-api",
                        "修复 Telegram Bot API 连通性",
                        "Bot token 无效或网络不可达。",
                        blocking=True,
                        sort_order=50,
                        manual_steps=[
                            "检查 TELEGRAM_BOT_TOKEN 是否正确",
                            "确认当前网络可访问 api.telegram.org",
                            "修复后重新运行: octo doctor",
                        ],
                    )
                ],
            )

        return ChannelStepResult(
            channel_id=self.channel_id,
            step="channel_readiness",
            status=OnboardingStepStatus.COMPLETED,
            summary=(
                f"Bot @{identity.username or identity.id} 连通，"
                f"approved_users={len(state.approved_users)}，"
                f"pending_pairings={len(state.pending_pairings)}。"
            ),
        )

    async def verify_first_message(self, project_root: Path, session: object) -> ChannelStepResult:
        del session
        availability = self.availability(project_root)
        if not availability.available:
            return ChannelStepResult(
                channel_id=self.channel_id,
                step="first_message",
                status=OnboardingStepStatus.BLOCKED,
                summary=availability.reason or "Telegram channel 不可用。",
                actions=availability.actions,
            )

        store = self._state_store_factory(project_root)
        state = store.load()
        if store.last_issue == "corrupted":
            backup_path = store.path.with_suffix(store.path.suffix + ".corrupted")
            return ChannelStepResult(
                channel_id=self.channel_id,
                step="first_message",
                status=OnboardingStepStatus.ACTION_REQUIRED,
                summary="telegram-state.json 已损坏，无法确认首条消息收件人。",
                actions=[
                    _manual_action(
                        "repair-telegram-state",
                        "修复 Telegram state",
                        "Telegram pairing state 无法解析。",
                        blocking=True,
                        sort_order=10,
                        manual_steps=[
                            f"检查备份文件: {backup_path}",
                            "修复或删除损坏文件后重新运行 onboarding",
                        ],
                    )
                ],
            )

        approved_user = state.first_approved_user()
        if approved_user is None:
            pending_count = len(state.pending_pairings)
            return ChannelStepResult(
                channel_id=self.channel_id,
                step="first_message",
                status=OnboardingStepStatus.ACTION_REQUIRED,
                summary="当前没有已批准的 Telegram DM 用户。",
                actions=[
                    _manual_action(
                        "complete-telegram-pairing",
                        "完成 Telegram pairing",
                        "先完成 DM pairing，再发送验证消息。",
                        blocking=True,
                        sort_order=20,
                        manual_steps=[
                            "启动 gateway，让目标用户先向 Bot 发送 /start 或任意消息",
                            (
                                "通过 Web operator inbox / Telegram callback 批准 pairing"
                                if pending_count
                                else (
                                    "通过 Web operator inbox 批准 pairing，"
                                    "或由 gateway pairing 流程写入 approved_users"
                                )
                            ),
                            (
                                "如需手工修复，可检查 "
                                f"{store.path.name} 中的 pending_pairings / approved_users"
                            ),
                            "重新运行: octo onboard --channel telegram",
                        ],
                    )
                ],
            )

        latest_task_id = await self._find_latest_inbound_task_id(
            project_root,
            approved_user.user_id,
            approved_user.chat_id,
        )
        if latest_task_id is not None:
            return ChannelStepResult(
                channel_id=self.channel_id,
                step="first_message",
                status=OnboardingStepStatus.COMPLETED,
                summary=(
                    f"已检测到 Telegram 入站任务 {latest_task_id}，"
                    f"用户 {approved_user.user_id} 的首条消息链路已闭环。"
                ),
            )

        try:
            sent = await self._client_factory(project_root).send_message(
                approved_user.chat_id,
                "OctoAgent onboarding 还差最后一步：请继续向 Bot 发送任意消息，验证入站链路。",
            )
        except TelegramBotClientConfigError as exc:
            return ChannelStepResult(
                channel_id=self.channel_id,
                step="first_message",
                status=OnboardingStepStatus.ACTION_REQUIRED,
                summary=str(exc),
                actions=[
                    _manual_action(
                        "set-telegram-token-for-first-message",
                        "设置 Telegram bot token",
                        str(exc),
                        blocking=True,
                        sort_order=30,
                        manual_steps=[
                            "确认 bot token 环境变量已生效",
                            "重新运行: octo onboard --channel telegram",
                        ],
                    )
                ],
            )
        except TelegramBotApiError as exc:
            blocking = exc.status_code not in {400, 403}
            return ChannelStepResult(
                channel_id=self.channel_id,
                step="first_message",
                status=(
                    OnboardingStepStatus.BLOCKED
                    if blocking
                    else OnboardingStepStatus.ACTION_REQUIRED
                ),
                summary=f"发送首条验证消息失败: {exc}",
                actions=[
                    _manual_action(
                        "repair-telegram-first-message",
                        "修复 Telegram 首条消息验证",
                        "确认目标用户已与 Bot 建立私聊并仍在 allowlist 内。",
                        blocking=blocking,
                        sort_order=40,
                        manual_steps=[
                            "让目标用户确认未屏蔽 Bot，且已发过 /start",
                            "确认 approved_users 中 chat_id 正确",
                            "修复后重新运行: octo onboard --channel telegram",
                        ],
                    )
                ],
            )

        store.upsert_approved_user(
            user_id=approved_user.user_id,
            chat_id=approved_user.chat_id,
            username=approved_user.username,
            display_name=approved_user.display_name,
            message_id=sent.message_id,
        )
        return ChannelStepResult(
            channel_id=self.channel_id,
            step="first_message",
            status=OnboardingStepStatus.ACTION_REQUIRED,
            summary=(
                f"已向 Telegram 用户 {approved_user.user_id} 发送验证提示"
                f"（message_id={sent.message_id}），但尚未检测到入站任务。"
            ),
            actions=[
                _manual_action(
                    "verify-telegram-ingress",
                    "完成 Telegram 首条入站验证",
                    "需要真实用户消息进入 gateway 并创建 task，onboarding 才算完成。",
                    blocking=True,
                    sort_order=50,
                    manual_steps=[
                        "启动 gateway，确保 Telegram webhook / polling 正在运行",
                        "让已批准用户向 Bot 再发送一条消息",
                        "重新运行: octo onboard --channel telegram",
                    ],
                )
            ],
        )

    @staticmethod
    def _resolve_db_path(project_root: Path) -> Path:
        db_path = Path(get_db_path()).expanduser()
        if db_path.is_absolute():
            return db_path
        return (project_root / db_path).resolve()

    async def _find_latest_inbound_task_id(
        self,
        project_root: Path,
        user_id: str | int,
        chat_id: str | int,
    ) -> str | None:
        db_path = self._resolve_db_path(project_root)
        if not db_path.exists():
            return None

        private_thread_id = f"tg:{user_id}"
        private_scope_id = f"chat:telegram:{chat_id}"

        try:
            async with aiosqlite.connect(f"file:{db_path}?mode=ro", uri=True) as conn:
                cursor = await conn.execute(
                    """
                    SELECT task_id
                    FROM tasks
                    WHERE json_extract(requester, '$.channel') = 'telegram'
                      AND json_extract(requester, '$.sender_id') = ?
                      AND scope_id = ?
                      AND thread_id = ?
                    ORDER BY created_at DESC
                    LIMIT 1
                    """,
                    (str(user_id), private_scope_id, private_thread_id),
                )
                row = await cursor.fetchone()
        except aiosqlite.Error:
            return None

        if row is None:
            return None
        return str(row[0])

    def _load_telegram_config(
        self,
        project_root: Path,
    ) -> tuple[TelegramChannelConfig | None, VerifierAvailability | None]:
        try:
            config = load_config(project_root)
        except ConfigParseError as exc:
            return None, VerifierAvailability(
                available=False,
                reason=f"octoagent.yaml 无法解析: {exc}",
                actions=[
                    _command_action(
                        "repair-octoagent-yaml-for-telegram",
                        "修复 octoagent.yaml",
                        "统一配置无法解析，Telegram verifier 无法继续。",
                        "octo config init --force",
                        blocking=True,
                        sort_order=5,
                    )
                ],
            )

        if config is None:
            return None, VerifierAvailability(
                available=False,
                reason="octoagent.yaml 不存在，无法读取 Telegram 配置。",
                actions=[
                    _command_action(
                        "init-octoagent-config",
                        "初始化统一配置",
                        "先生成 octoagent.yaml，再配置 Telegram channel。",
                        "octo config init",
                        blocking=True,
                        sort_order=1,
                    )
                ],
            )
        return config.channels.telegram, None


def build_builtin_verifier_registry(
    *,
    environ: Mapping[str, str] | None = None,
    client_factory: TelegramBotClientFactory | None = None,
    state_store_factory: TelegramStateStoreFactory | None = None,
) -> ChannelVerifierRegistry:
    """构造 provider/dx 默认 registry。"""

    registry = ChannelVerifierRegistry()
    registry.register(
        TelegramOnboardingVerifier(
            environ=environ,
            client_factory=client_factory,
            state_store_factory=state_store_factory,
        )
    )
    return registry
