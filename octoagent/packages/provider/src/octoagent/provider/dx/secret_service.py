"""Feature 025: project-scoped secret lifecycle。"""

from __future__ import annotations

import os
from collections.abc import AsyncIterator, Iterable
from contextlib import asynccontextmanager, contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from dotenv import dotenv_values
from octoagent.core.models import (
    Project,
    ProjectSecretBinding,
    SecretBindingStatus,
    SecretRefSourceType,
    SecretTargetKind,
)
from octoagent.core.store import StoreGroup, create_store_group
from pydantic import SecretStr
from ulid import ULID

from ..auth.store import CredentialStore
from .backup_service import resolve_artifacts_dir, resolve_db_path, resolve_project_root
from .config_schema import OctoAgentConfig
from .config_wizard import load_config
from .project_migration import ProjectWorkspaceMigrationService
from .secret_models import (
    RuntimeSecretMaterialization,
    SecretApplyRun,
    SecretAuditReport,
    SecretConfigureSummary,
    SecretRef,
    SecretReloadResult,
)
from .secret_refs import SecretResolutionError, inspect_secret_ref, resolve_secret_ref
from .secret_status_store import SecretStatusStore
from .update_service import UpdateActionError, UpdateService
from .update_status_store import UpdateStatusStore


def _utc_now() -> datetime:
    return datetime.now(tz=UTC)


class SecretServiceError(RuntimeError):
    def __init__(self, message: str, *, exit_code: int = 2) -> None:
        super().__init__(message)
        self.message = message
        self.exit_code = exit_code


@dataclass(frozen=True)
class _TargetSpec:
    target_kind: SecretTargetKind
    target_key: str
    env_name: str
    display_name: str
    required: bool = True
    provider_id: str | None = None


class SecretService:
    """`octo secrets *` 的生命周期服务。"""

    def __init__(
        self,
        project_root: Path,
        *,
        store_group: StoreGroup | None = None,
        credential_store: CredentialStore | None = None,
        environ: dict[str, str] | None = None,
    ) -> None:
        self._root = resolve_project_root(project_root).resolve()
        self._db_path = resolve_db_path(self._root)
        self._artifacts_dir = resolve_artifacts_dir(self._root)
        self._store_group = store_group
        self._credential_store = credential_store or CredentialStore()
        self._environ = environ if environ is not None else os.environ
        self._status_store = SecretStatusStore(self._root)
        self._update_status_store = UpdateStatusStore(self._root)

    async def audit(self, project_ref: str | None = None) -> SecretAuditReport:
        await self._ensure_migration_ready()
        async with self._store_group_scope() as store_group:
            project = await self._resolve_project(store_group, project_ref)
            config = self._load_config_or_raise()
            report = SecretAuditReport(
                report_id=f"secret-audit-{str(ULID()).lower()}",
                project_id=project.project_id,
            )
            status_store = self._status_store.for_project(project.project_id)
            bindings = await store_group.project_store.list_secret_bindings(project.project_id)
            binding_map = {
                (binding.target_kind, binding.target_key): binding
                for binding in bindings
            }
            target_specs = self._collect_target_specs(config)
            for spec in target_specs:
                binding = binding_map.get((spec.target_kind, spec.target_key))
                if binding is None:
                    if self._has_legacy_env_bridge(spec.env_name):
                        report.warnings.append(
                            f"{spec.display_name} 尚未绑定，"
                            f"但检测到 legacy env bridge: {spec.env_name}"
                        )
                        continue
                    if self._has_provider_profile_bridge(spec):
                        report.warnings.append(
                            f"{spec.display_name} 当前依赖 provider auth profile bridge。"
                        )
                        continue
                    if spec.required:
                        report.missing_targets.append(spec.target_key)
                    continue

                ref = self._binding_to_ref(binding)
                report.plaintext_risks.extend(inspect_secret_ref(ref, project_root=self._root))
                try:
                    resolve_secret_ref(ref, environ=dict(self._environ), cwd=self._root)
                except SecretResolutionError as exc:
                    report.unresolved_refs.append(f"{binding.target_key}: {exc.code}")
                    continue
                if binding.status in {
                    SecretBindingStatus.DRAFT,
                    SecretBindingStatus.INVALID,
                    SecretBindingStatus.NEEDS_RELOAD,
                    SecretBindingStatus.ROTATION_PENDING,
                }:
                    report.reload_required = True

            materialization = status_store.load_materialization()
            if bindings and materialization is None:
                report.reload_required = True
            if report.unresolved_refs or report.plaintext_risks:
                report.overall_status = "blocked"
            elif (
                report.missing_targets
                or report.conflicts
                or report.reload_required
                or report.warnings
            ):
                report.overall_status = "action_required"
            else:
                report.overall_status = "ready"
            report.restart_required = report.reload_required
            return report

    async def configure(
        self,
        *,
        project_ref: str | None = None,
        source_type: SecretRefSourceType,
        locator: dict[str, Any],
        target_keys: list[str] | None = None,
        rotate: bool = False,
    ) -> SecretConfigureSummary:
        await self._ensure_migration_ready()
        async with self._store_group_scope() as store_group:
            project = await self._resolve_project(store_group, project_ref)
            config = self._load_config_or_raise()
            all_specs = self._collect_target_specs(config)
            selected_specs = self._select_target_specs(all_specs, target_keys)
            if not selected_specs:
                raise SecretServiceError("没有可配置的 secret targets。")

            warnings: list[str] = []
            configured_targets: list[str] = []
            now = _utc_now()
            for spec in selected_specs:
                ref_locator = self._normalize_locator(
                    spec=spec,
                    source_type=source_type,
                    locator=locator,
                )
                binding = ProjectSecretBinding(
                    binding_id=f"secret-binding-{str(ULID()).lower()}",
                    project_id=project.project_id,
                    target_kind=spec.target_kind,
                    target_key=spec.target_key,
                    env_name=spec.env_name,
                    ref_source_type=source_type,
                    ref_locator=ref_locator,
                    display_name=spec.display_name,
                    status=(
                        SecretBindingStatus.ROTATION_PENDING
                        if rotate
                        else SecretBindingStatus.DRAFT
                    ),
                    last_audited_at=now,
                    metadata={
                        "configured_at": now.isoformat(),
                        "configured_by": "cli",
                        "project_slug": project.slug,
                    },
                    updated_at=now,
                )
                await store_group.project_store.save_secret_binding(binding)
                configured_targets.append(spec.target_key)
                if source_type == SecretRefSourceType.KEYCHAIN and not ref_locator.get("account"):
                    warnings.append(f"{spec.target_key} 使用 keychain 时建议显式指定 account。")
            await store_group.conn.commit()
            return SecretConfigureSummary(
                project_id=project.project_id,
                source_default=source_type.value,
                configured_targets=configured_targets,
                warnings=warnings,
                next_actions=[
                    "运行 octo secrets audit 查看解析状态。",
                    "运行 octo secrets apply 写入 canonical binding 状态。",
                ],
            )

    async def apply(
        self,
        *,
        project_ref: str | None = None,
        dry_run: bool = False,
    ) -> SecretApplyRun:
        await self._ensure_migration_ready()
        async with self._store_group_scope() as store_group:
            project = await self._resolve_project(store_group, project_ref)
            bindings = await store_group.project_store.list_secret_bindings(project.project_id)
            if not bindings:
                raise SecretServiceError(
                    "当前 project 还没有 secret bindings，请先运行 octo secrets configure。"
                )
            status_store = self._status_store.for_project(project.project_id)

            run = SecretApplyRun(
                run_id=f"secret-apply-{str(ULID()).lower()}",
                project_id=project.project_id,
                dry_run=dry_run,
                status="running",
                planned_binding_ids=[binding.binding_id for binding in bindings],
                started_at=_utc_now(),
            )
            issues: list[str] = []
            resolved_env_names: list[str] = []
            resolved_targets: list[str] = []
            for binding in bindings:
                ref = self._binding_to_ref(binding)
                try:
                    resolve_secret_ref(ref, environ=dict(self._environ), cwd=self._root)
                except SecretResolutionError as exc:
                    issues.append(f"{binding.target_key}: {exc.code}")
                else:
                    resolved_env_names.append(binding.env_name)
                    resolved_targets.append(binding.target_key)

            run.materialization_summary = {
                "resolved_env_names": sorted(set(resolved_env_names)),
                "resolved_targets": sorted(set(resolved_targets)),
                "delivery_mode": self._delivery_mode(),
                "requires_restart": True,
            }
            run.issues = issues
            run.reload_required = not dry_run and not issues
            if dry_run:
                run.status = "dry_run"
            elif issues:
                run.status = "failed"
            else:
                run.status = "applied"
                run.applied_binding_ids = [binding.binding_id for binding in bindings]
                now = _utc_now()
                for binding in bindings:
                    await store_group.project_store.save_secret_binding(
                        binding.model_copy(
                            update={
                                "status": SecretBindingStatus.NEEDS_RELOAD,
                                "last_applied_at": now,
                                "updated_at": now,
                            }
                        )
                    )
            run.completed_at = _utc_now()
            if issues and not dry_run:
                now = _utc_now()
                for binding in bindings:
                    if any(issue.startswith(f"{binding.target_key}:") for issue in issues):
                        await store_group.project_store.save_secret_binding(
                            binding.model_copy(
                                update={
                                    "status": SecretBindingStatus.INVALID,
                                    "last_audited_at": now,
                                    "updated_at": now,
                                }
                            )
                        )
            await store_group.conn.commit()
            status_store.save_apply(run)
            return run

    async def rotate(
        self,
        *,
        project_ref: str | None = None,
        source_type: SecretRefSourceType,
        locator: dict[str, Any],
        target_keys: list[str] | None = None,
    ) -> SecretConfigureSummary:
        return await self.configure(
            project_ref=project_ref,
            source_type=source_type,
            locator=locator,
            target_keys=target_keys,
            rotate=True,
        )

    async def reload(self, *, project_ref: str | None = None) -> SecretReloadResult:
        await self._ensure_migration_ready()
        async with self._store_group_scope() as store_group:
            project = await self._resolve_project(store_group, project_ref)
            bindings = await store_group.project_store.list_secret_bindings(project.project_id)
            if not bindings:
                raise SecretServiceError("当前 project 没有 secret bindings，无法 reload。")
            status_store = self._status_store.for_project(project.project_id)

            resolved_pairs: list[tuple[ProjectSecretBinding, SecretStr]] = []
            for binding in bindings:
                ref = self._binding_to_ref(binding)
                try:
                    resolved = resolve_secret_ref(ref, environ=dict(self._environ), cwd=self._root)
                except SecretResolutionError as exc:
                    raise SecretServiceError(
                        f"secret reload 失败：{binding.target_key} 无法解析（{exc.code}）。"
                    ) from exc
                resolved_pairs.append((binding, resolved.value))

            snapshot = RuntimeSecretMaterialization(
                snapshot_id=f"secret-materialization-{str(ULID()).lower()}",
                project_id=project.project_id,
                resolved_env_names=sorted({binding.env_name for binding, _ in resolved_pairs}),
                resolved_targets=sorted({binding.target_key for binding, _ in resolved_pairs}),
                delivery_mode=self._delivery_mode(),
                requires_restart=True,
                expires_at=_utc_now() + timedelta(minutes=10),
            )
            status_store.save_materialization(snapshot)
            descriptor = self._update_status_store.load_runtime_descriptor()
            now = _utc_now()
            if descriptor is None:
                for binding in bindings:
                    await store_group.project_store.save_secret_binding(
                        binding.model_copy(
                            update={
                                "status": SecretBindingStatus.APPLIED,
                                "last_reloaded_at": now,
                                "updated_at": now,
                            }
                        )
                    )
                await store_group.conn.commit()
                return SecretReloadResult(
                    project_id=project.project_id,
                    overall_status="action_required",
                    summary="当前 runtime 未托管，已生成 materialization 摘要但未自动 reload。",
                    materialization=snapshot,
                    warnings=["unmanaged runtime 无法自动 restart/verify。"],
                    actions=[
                        "在当前 shell 导出对应 env 后手动重启 gateway/runtime。",
                        "如需自动 reload，请先使用 024 的 install/update 链路托管 runtime。",
                    ],
                )

            with self._temporary_env(
                {
                    binding.env_name: secret.get_secret_value()
                    for binding, secret in resolved_pairs
                }
            ):
                service = UpdateService(self._root)
                try:
                    restart = await service.restart(trigger_source="cli")
                    verify = await service.verify(trigger_source="cli")
                except UpdateActionError as exc:
                    raise SecretServiceError(f"secret reload 失败：{exc.message}") from exc

            for binding in bindings:
                await store_group.project_store.save_secret_binding(
                    binding.model_copy(
                        update={
                            "status": SecretBindingStatus.APPLIED,
                            "last_reloaded_at": now,
                            "updated_at": now,
                        }
                    )
                )
            await store_group.conn.commit()
            return SecretReloadResult(
                project_id=project.project_id,
                overall_status="completed",
                summary=(
                    "managed runtime 已完成 restart + verify。"
                    f" restart={restart.overall_status} verify={verify.overall_status}"
                ),
                materialization=snapshot,
            )

    async def _ensure_migration_ready(self) -> None:
        migration = ProjectWorkspaceMigrationService(self._root)
        await migration.ensure_default_project()

    async def _resolve_project(
        self,
        store_group: StoreGroup,
        project_ref: str | None,
    ) -> Project:
        if project_ref:
            project = await store_group.project_store.resolve_project(project_ref)
            if project is None:
                raise SecretServiceError(f"未找到 project: {project_ref}")
            return project

        selector = await store_group.project_store.get_selector_state("cli")
        if selector is not None:
            project = await store_group.project_store.get_project(selector.active_project_id)
            if project is not None:
                return project
        project = await store_group.project_store.get_default_project()
        if project is None:
            raise SecretServiceError("当前没有可用 project，请先运行 octo project create。")
        return project

    def _load_config_or_raise(self) -> OctoAgentConfig:
        cfg = load_config(self._root)
        if cfg is None:
            raise SecretServiceError("当前缺少 octoagent.yaml，无法推导 secret targets。")
        return cfg

    def _collect_target_specs(self, config: OctoAgentConfig) -> list[_TargetSpec]:
        targets: list[_TargetSpec] = []
        if config.runtime.llm_mode == "litellm" and config.runtime.master_key_env:
            targets.append(
                _TargetSpec(
                    target_kind=SecretTargetKind.RUNTIME,
                    target_key="runtime.master_key_env",
                    env_name=config.runtime.master_key_env,
                    display_name="LiteLLM Master Key",
                )
            )
        for provider in config.providers:
            if provider.enabled and provider.auth_type == "api_key" and provider.api_key_env:
                targets.append(
                    _TargetSpec(
                        target_kind=SecretTargetKind.PROVIDER,
                        target_key=f"providers.{provider.id}.api_key_env",
                        env_name=provider.api_key_env,
                        display_name=f"{provider.name} API Key",
                        provider_id=provider.id,
                    )
                )
        telegram = config.channels.telegram
        if telegram.enabled and telegram.bot_token_env:
            targets.append(
                _TargetSpec(
                    target_kind=SecretTargetKind.CHANNEL,
                    target_key="channels.telegram.bot_token_env",
                    env_name=telegram.bot_token_env,
                    display_name="Telegram Bot Token",
                )
            )
        if telegram.enabled and telegram.mode == "webhook" and telegram.webhook_secret_env:
            targets.append(
                _TargetSpec(
                    target_kind=SecretTargetKind.CHANNEL,
                    target_key="channels.telegram.webhook_secret_env",
                    env_name=telegram.webhook_secret_env,
                    display_name="Telegram Webhook Secret",
                )
            )
        return targets

    @staticmethod
    def _select_target_specs(
        specs: list[_TargetSpec],
        target_keys: list[str] | None,
    ) -> list[_TargetSpec]:
        if not target_keys:
            return specs
        wanted = set(target_keys)
        return [spec for spec in specs if spec.target_key in wanted]

    def _normalize_locator(
        self,
        *,
        spec: _TargetSpec,
        source_type: SecretRefSourceType,
        locator: dict[str, Any],
    ) -> dict[str, Any]:
        normalized = dict(locator)
        if source_type == SecretRefSourceType.ENV:
            normalized.setdefault("env_name", normalized.get("env_name") or spec.env_name)
        if source_type == SecretRefSourceType.KEYCHAIN:
            normalized.setdefault("service", "octoagent")
            normalized.setdefault("account", f"{spec.target_key}@{spec.env_name}")
        return normalized

    def _binding_to_ref(self, binding: ProjectSecretBinding) -> SecretRef:
        return SecretRef(
            source_type=binding.ref_source_type,
            locator=dict(binding.ref_locator),
            display_name=binding.display_name,
            redaction_label=binding.redaction_label,
            metadata=dict(binding.metadata),
        )

    def _has_legacy_env_bridge(self, env_name: str) -> bool:
        if self._environ.get(env_name, ""):
            return True
        for env_path in (self._root / ".env", self._root / ".env.litellm"):
            if not env_path.exists():
                continue
            try:
                payload = dotenv_values(env_path)
            except Exception:
                continue
            if payload.get(env_name):
                return True
        return False

    def _has_provider_profile_bridge(self, spec: _TargetSpec) -> bool:
        if spec.provider_id is None:
            return False
        for profile in self._credential_store.list_profiles():
            if profile.provider == spec.provider_id:
                return True
        return False

    def _delivery_mode(self) -> str:
        return (
            "managed_restart_verify"
            if self._update_status_store.load_runtime_descriptor() is not None
            else "unmanaged_manual"
        )

    @contextmanager
    def _temporary_env(self, entries: dict[str, str]) -> Iterable[None]:
        previous = {key: self._environ.get(key) for key in entries}
        try:
            for key, value in entries.items():
                self._environ[key] = value
            yield
        finally:
            for key, old in previous.items():
                if old is None:
                    self._environ.pop(key, None)
                else:
                    self._environ[key] = old

    @asynccontextmanager
    async def _store_group_scope(self) -> AsyncIterator[StoreGroup]:
        if self._store_group is not None:
            yield self._store_group
            return

        store_group = await create_store_group(str(self._db_path), self._artifacts_dir)
        try:
            yield store_group
        finally:
            await store_group.conn.close()
