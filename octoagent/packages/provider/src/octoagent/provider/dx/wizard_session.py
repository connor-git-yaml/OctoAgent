"""Feature 025: CLI unified wizard session adapter。"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import click
from octoagent.core.models import Project
from pydantic import BaseModel
from ulid import ULID

from .config_schema import (
    ChannelsConfig,
    ModelAlias,
    OctoAgentConfig,
    ProviderEntry,
    RuntimeConfig,
    TelegramChannelConfig,
    build_config_schema_document,
)
from .config_wizard import load_config
from .control_plane_models import (
    ConfigSchemaDocument,
    WizardNextAction,
    WizardSessionDocument,
    WizardStepState,
)
from .wizard_session_store import WizardSessionRecord, WizardSessionStore


def _utc_now() -> datetime:
    return datetime.now(tz=UTC)


class WizardSessionResult(BaseModel):
    """wizard 操作返回。"""

    record: WizardSessionRecord
    resumed: bool = False
    schema_document: ConfigSchemaDocument


class WizardSessionService:
    """将 026-A wizard contract 落到 CLI。"""

    def __init__(self, project_root: Path, *, surface: str = "cli") -> None:
        self._root = project_root.resolve()
        self._surface = surface
        self._store = WizardSessionStore(self._root, surface=surface)

    def load_status(self, project_id: str) -> WizardSessionResult | None:
        record = self._store.load()
        if record is None or record.project_id != project_id:
            return None
        schema = build_config_schema_document(self._load_existing_config())
        return WizardSessionResult(record=record, resumed=True, schema_document=schema)

    def cancel(self, project_id: str) -> WizardSessionResult | None:
        result = self.load_status(project_id)
        if result is None:
            return None
        record = result.record.model_copy(
            update={
                "status": "cancelled",
                "blocking_reason": "用户主动取消 wizard session。",
                "document": result.record.document.model_copy(
                    update={
                        "status": "cancelled",
                        "blocking_reason": "用户主动取消 wizard session。",
                        "updated_at": _utc_now(),
                    }
                ),
            }
        )
        self._store.save(record)
        return WizardSessionResult(
            record=record,
            resumed=False,
            schema_document=result.schema_document,
        )

    def apply_current_session(self, project_id: str) -> WizardSessionResult:
        result = self.load_status(project_id)
        if result is None:
            raise ValueError("当前 project 没有可应用的 wizard draft。")
        record = result.record
        next_actions = [
            {
                "title": "配置 secrets",
                "description": "生成或更新当前 project 的 secret bindings。",
                "command": "octo secrets configure",
                "blocking": True,
            },
            {
                "title": "应用 secrets",
                "description": "把 binding 计划写入 canonical store。",
                "command": "octo secrets apply",
                "blocking": True,
            },
            {
                "title": "reload runtime",
                "description": "把当前 project secrets 注入 runtime。",
                "command": "octo secrets reload",
                "blocking": True,
            },
        ]
        document = record.document.model_copy(
            update={
                "status": "action_required",
                "current_step": "secrets",
                "blocking_reason": "配置已写入，等待 secrets configure/apply/reload。",
                "updated_at": _utc_now(),
                "step_states": [
                    step.model_copy(
                        update={
                            "status": (
                                "in_progress"
                                if step.step_id == "secrets"
                                else "completed"
                            ),
                        }
                    )
                    for step in record.document.step_states
                ],
                "next_actions": [
                    WizardNextAction(
                        action_id=f"wizard-secrets-{index}",
                        title=item["title"],
                        description=item["description"],
                        command=item["command"],
                        blocking=item["blocking"],
                    )
                    for index, item in enumerate(next_actions, start=1)
                ],
            }
        )
        updated = record.model_copy(
            update={
                "current_step_id": "secrets",
                "status": "action_required",
                "blocking_reason": "配置已写入，等待 secrets configure/apply/reload。",
                "document": document,
                "next_actions": next_actions,
            }
        )
        self._store.save(updated)
        return WizardSessionResult(
            record=updated,
            resumed=False,
            schema_document=result.schema_document,
        )

    def start_or_resume(
        self,
        project: Project,
        *,
        interactive: bool,
        advanced: bool = False,
    ) -> WizardSessionResult:
        existing = self.load_status(project.project_id)
        config = self._load_existing_config()
        schema = build_config_schema_document(config)
        if existing is not None and existing.record.status not in {"cancelled", "completed"}:
            if interactive:
                return self._drive_cli(
                    existing.record,
                    schema,
                    project,
                    advanced=advanced,
                    resumed=True,
                )
            return WizardSessionResult(
                record=existing.record,
                resumed=True,
                schema_document=schema,
            )

        record = self._new_record(project, schema, config)
        self._store.save(record)
        if not interactive:
            return WizardSessionResult(
                record=record,
                resumed=False,
                schema_document=schema,
            )
        return self._drive_cli(record, schema, project, advanced=advanced, resumed=False)

    def _drive_cli(
        self,
        record: WizardSessionRecord,
        schema: ConfigSchemaDocument,
        project: Project,
        *,
        advanced: bool,
        resumed: bool,
    ) -> WizardSessionResult:
        current_config = self._load_existing_config()
        field_hints = schema.ui_hints.get("fields", {})

        provider_id = self._prompt_field(
            field_hints,
            "providers.0.id",
            advanced=advanced,
        )
        provider_name = self._prompt_field(
            field_hints,
            "providers.0.name",
            advanced=advanced,
        )
        auth_type = self._prompt_choice(field_hints, "providers.0.auth_type")
        api_key_env = self._prompt_field(
            field_hints,
            "providers.0.api_key_env",
            advanced=advanced,
        )
        main_model = self._prompt_field(
            field_hints,
            "model_aliases.main.model",
            advanced=advanced,
        )
        cheap_model = self._prompt_field(
            field_hints,
            "model_aliases.cheap.model",
            advanced=advanced,
        )
        llm_mode = self._prompt_choice(field_hints, "runtime.llm_mode")
        proxy_url = self._prompt_field(
            field_hints,
            "runtime.litellm_proxy_url",
            advanced=advanced,
        )
        master_key_env = self._prompt_field(
            field_hints,
            "runtime.master_key_env",
            advanced=advanced,
        )
        telegram_enabled = self._prompt_confirm(field_hints, "channels.telegram.enabled")
        telegram_mode = current_config.channels.telegram.mode if current_config else "polling"
        telegram_bot_token_env = (
            current_config.channels.telegram.bot_token_env
            if current_config
            else "TELEGRAM_BOT_TOKEN"
        )
        telegram_webhook_url = (
            current_config.channels.telegram.webhook_url if current_config else ""
        )
        telegram_webhook_secret_env = (
            current_config.channels.telegram.webhook_secret_env if current_config else ""
        )
        if telegram_enabled:
            telegram_mode = self._prompt_choice(field_hints, "channels.telegram.mode")
            telegram_bot_token_env = self._prompt_field(
                field_hints,
                "channels.telegram.bot_token_env",
                advanced=advanced,
            )
            if telegram_mode == "webhook":
                telegram_webhook_url = self._prompt_field(
                    field_hints,
                    "channels.telegram.webhook_url",
                    advanced=advanced,
                )
                telegram_webhook_secret_env = self._prompt_field(
                    field_hints,
                    "channels.telegram.webhook_secret_env",
                    advanced=advanced,
                )
            else:
                telegram_webhook_url = ""
                telegram_webhook_secret_env = ""

        config = OctoAgentConfig(
            updated_at=_utc_now().date().isoformat(),
            providers=[
                ProviderEntry(
                    id=provider_id,
                    name=provider_name,
                    auth_type=auth_type,
                    api_key_env=api_key_env,
                )
            ],
            model_aliases={
                "main": ModelAlias(
                    provider=provider_id,
                    model=main_model,
                    description="wizard main alias",
                ),
                "cheap": ModelAlias(
                    provider=provider_id,
                    model=cheap_model,
                    description="wizard cheap alias",
                ),
            },
            runtime=RuntimeConfig(
                llm_mode=llm_mode,
                litellm_proxy_url=proxy_url,
                master_key_env=master_key_env,
            ),
            channels=ChannelsConfig(
                telegram=TelegramChannelConfig(
                    enabled=telegram_enabled,
                    mode=telegram_mode,
                    bot_token_env=telegram_bot_token_env,
                    webhook_url=telegram_webhook_url,
                    webhook_secret_env=telegram_webhook_secret_env,
                )
            ),
        )
        draft_secret_bindings = self._build_draft_secret_bindings(config)
        next_actions = [
            {
                "title": "应用配置",
                "description": "确认 draft config 后写入 octoagent.yaml。",
                "command": "octo project edit --apply-wizard",
                "blocking": False,
            },
            {
                "title": "配置 secrets",
                "description": "根据 draft secret 计划生成或更新 project secret bindings。",
                "command": "octo secrets configure",
                "blocking": True,
            },
        ]
        document = record.document.model_copy(
            update={
                "status": "ready_for_apply",
                "current_step": "review",
                "blocking_reason": "配置草案已生成，等待 apply + secrets lifecycle。",
                "step_states": [
                    WizardStepState(step_id="project", title="Project", status="completed"),
                    WizardStepState(step_id="provider", title="Provider", status="completed"),
                    WizardStepState(step_id="runtime", title="Runtime", status="completed"),
                    WizardStepState(
                        step_id="telegram",
                        title="Telegram",
                        status="completed" if telegram_enabled else "skipped",
                        summary="已收集 Telegram channel 配置。"
                        if telegram_enabled
                        else "未启用 Telegram。",
                    ),
                    WizardStepState(
                        step_id="secrets",
                        title="Secrets",
                        status="pending",
                        summary="等待 octo secrets configure/apply/reload。",
                    ),
                    WizardStepState(
                        step_id="review",
                        title="Review",
                        status="in_progress",
                        summary="当前展示的是 redacted draft config。",
                    ),
                ],
                "next_actions": [
                    WizardNextAction(
                        action_id=f"wizard-next-{index}",
                        title=item["title"],
                        description=item["description"],
                        command=item["command"],
                        blocking=item["blocking"],
                    )
                    for index, item in enumerate(next_actions, start=1)
                ],
                "updated_at": _utc_now(),
            }
        )
        updated = record.model_copy(
            update={
                "current_step_id": "review",
                "status": "ready_for_apply",
                "blocking_reason": "配置草案已生成，等待 apply + secrets lifecycle。",
                "draft_config": config.model_dump(mode="json"),
                "draft_secret_bindings": draft_secret_bindings,
                "next_actions": next_actions,
                "document": document,
            }
        )
        self._store.save(updated)
        return WizardSessionResult(
            record=updated,
            resumed=resumed,
            schema_document=schema,
        )

    def _new_record(
        self,
        project: Project,
        schema: ConfigSchemaDocument,
        config: OctoAgentConfig | None,
    ) -> WizardSessionRecord:
        session_id = f"wizard-{str(ULID()).lower()}"
        document = WizardSessionDocument(
            resource_id=session_id,
            project_id=project.project_id,
            current_step="provider",
            status="pending",
            blocking_reason="等待 CLI wizard 收集 provider/runtime/channel 配置。",
            step_states=[
                WizardStepState(
                    step_id="project",
                    title="Project",
                    status="completed",
                    summary=f"当前 project: {project.slug}",
                ),
                WizardStepState(
                    step_id="provider",
                    title="Provider",
                    status="in_progress",
                    summary="等待输入 provider 基础信息。",
                ),
                WizardStepState(step_id="runtime", title="Runtime", status="pending"),
                WizardStepState(step_id="telegram", title="Telegram", status="pending"),
                WizardStepState(step_id="secrets", title="Secrets", status="pending"),
                WizardStepState(step_id="review", title="Review", status="pending"),
            ],
            next_actions=[
                WizardNextAction(
                    action_id="wizard-fill-config",
                    title="填写配置字段",
                    description="继续 CLI wizard 收集 provider/runtime/channel 配置。",
                    command="octo project edit --wizard",
                )
            ],
            schema_ref=schema.resource_id,
        )
        return WizardSessionRecord(
            session_id=session_id,
            project_id=project.project_id,
            current_step_id="provider",
            draft_config=config.model_dump(mode="json") if config is not None else {},
            document=document,
            next_actions=[
                {
                    "title": "填写配置字段",
                    "description": "继续 CLI wizard 收集 provider/runtime/channel 配置。",
                    "command": "octo project edit --wizard",
                    "blocking": True,
                }
            ],
        )

    def _load_existing_config(self) -> OctoAgentConfig | None:
        return load_config(self._root)

    @staticmethod
    def _prompt_field(field_hints: dict[str, Any], field_path: str, *, advanced: bool) -> str:
        hint = field_hints[field_path]
        if advanced and hint.get("secret_target"):
            click.echo(
                f"[advanced] {field_path} -> secret_target={hint['secret_target']['target_key']}"
            )
        return str(click.prompt(hint["label"], default=hint.get("default", "")))

    @staticmethod
    def _prompt_choice(field_hints: dict[str, Any], field_path: str) -> str:
        hint = field_hints[field_path]
        choices = hint.get("choices", [])
        label = f"{hint['label']} ({'/'.join(choices)})" if choices else hint["label"]
        return str(click.prompt(label, default=hint.get("default", "")))

    @staticmethod
    def _prompt_confirm(field_hints: dict[str, Any], field_path: str) -> bool:
        hint = field_hints[field_path]
        return bool(click.confirm(hint["label"], default=bool(hint.get("default", False))))

    @staticmethod
    def _build_draft_secret_bindings(config: OctoAgentConfig) -> list[dict[str, Any]]:
        targets: list[dict[str, Any]] = []
        if config.runtime.master_key_env and config.runtime.llm_mode == "litellm":
            targets.append(
                {
                    "target_kind": "runtime",
                    "target_key": "runtime.master_key_env",
                    "env_name": config.runtime.master_key_env,
                    "display_name": "LiteLLM Master Key",
                }
            )
        for provider in config.providers:
            if provider.auth_type == "api_key" and provider.api_key_env:
                targets.append(
                    {
                        "target_kind": "provider",
                        "target_key": f"providers.{provider.id}.api_key_env",
                        "env_name": provider.api_key_env,
                        "display_name": f"{provider.name} API Key",
                    }
                )
        if config.memory.backend_mode == "memu" and config.memory.bridge_api_key_env:
            targets.append(
                {
                    "target_kind": "memory",
                    "target_key": "memory.bridge_api_key_env",
                    "env_name": config.memory.bridge_api_key_env,
                    "display_name": "MemU Bridge API Key",
                }
            )
        telegram = config.channels.telegram
        if telegram.enabled and telegram.bot_token_env:
            targets.append(
                {
                    "target_kind": "channel",
                    "target_key": "channels.telegram.bot_token_env",
                    "env_name": telegram.bot_token_env,
                    "display_name": "Telegram Bot Token",
                }
            )
        if telegram.enabled and telegram.mode == "webhook" and telegram.webhook_secret_env:
            targets.append(
                {
                    "target_kind": "channel",
                    "target_key": "channels.telegram.webhook_secret_env",
                    "env_name": telegram.webhook_secret_env,
                    "display_name": "Telegram Webhook Secret",
                }
            )
        return targets
