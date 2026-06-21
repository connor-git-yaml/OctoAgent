"""PluginRegistry —— F106 用户插件装载编排器（Phase A declarative + Phase B code）。

职责：发现（纯 stat）→ 校验 → 能力分类 → 声明式制品威胁扫描 → 注册（skill→SkillDiscovery /
code 工具→中央 ToolRegistry 专用 loader）→ toggle / approve / refresh / remove → 审计事件，
全程 `asyncio.Lock` 串行 + 单 plugin try/except 降级隔离（#6）。

信任模型 spec §0.2/§0.3：declarative 自由装；code-capable 启用须用户显式审批（human-initiated）+
整树 code_hash 绑定；**未审批不 import**；换码强制重审。**v0.1 无沙箱**——已审批=进程内任意代码。

Phase A 暂缓 behavior overlay（FR-3.5/US4，KNOWLEDGE.md fallback）——见 completion-report，
additive 低风险，作为 Phase A 收尾 follow-up；本类已记录 record.provides.behavior 作扩展点。
"""

from __future__ import annotations

import asyncio
import shutil
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import structlog
from ulid import ULID

from octoagent.core.models.enums import ActorType, EventType, TaskStatus
from octoagent.core.models.event import Event
from octoagent.skills.discovery import SkillDiscovery
from octoagent.skills.plugins.approval import (
    clear_approval,
    is_approved,
    write_approval,
)
from octoagent.skills.plugins.code_hash import compute_tree_hash
from octoagent.skills.plugins.discovery import (
    PluginValidationError,
    classify,
    iter_plugin_dirs,
    load_manifest,
    validate_no_symlinks,
    validate_provides,
)
from octoagent.skills.plugins.manifest import (
    PLUGIN_DISABLED_MARKER,
    PLUGIN_MANIFEST_FILE,
    PluginCapability,
    PluginManifest,
    PluginRecord,
    PluginRejectedReason,
    PluginState,
)

from .plugin_git import GitError, git_install, git_update, is_git_plugin
from .plugin_loader import (
    LoadedPluginCode,
    PluginLoadError,
    load_plugin_tools,
    unload_plugin_code,
)

log = structlog.get_logger(__name__)

PLUGIN_AUDIT_TASK_ID = "_plugin_registry_audit"


class PluginRegistry:
    """用户插件装载编排器（单例，bootstrap 段 7.5 构造）。"""

    def __init__(
        self,
        *,
        plugins_dir: Path,
        skill_discovery: SkillDiscovery,
        content_scanner: Any,
        tool_registry: Any,
        event_store: Any | None = None,
        task_store: Any | None = None,
    ) -> None:
        self._plugins_dir = plugins_dir
        self._skill_discovery = skill_discovery
        self._scanner = content_scanner  # ContentThreatScanService（scan_memory）
        self._tool_registry = tool_registry  # 中央 ToolRegistry
        self._event_store = event_store
        self._task_store = task_store
        self._lock = asyncio.Lock()
        self._records: dict[str, PluginRecord] = {}
        self._loaded_code: dict[str, LoadedPluginCode] = {}
        self._audit_task_ready = False

    # ----------------------------------------------------------------- 公共 API

    async def discover_and_register(self) -> None:
        """发现并注册全部 plugin（bootstrap + refresh 入口）。"""
        async with self._lock:
            await self._reconcile_locked()

    async def refresh(self) -> dict[str, int]:
        """重扫 + 原子更新，返回计数摘要。"""
        async with self._lock:
            await self._reconcile_locked()
            return self._counts()

    async def approve(self, name: str) -> PluginRecord | None:
        """审批 code plugin（human-initiated）：记当前整树 code_hash + 加载代码。

        Returns:
            审批后的 PluginRecord；plugin 不存在/非 code/已是 declarative 返回 None。
        """
        async with self._lock:
            plugin_dir = self._plugins_dir / name
            if not (plugin_dir / PLUGIN_MANIFEST_FILE).is_file():
                return None
            record = self._records.get(name)
            if record is not None and record.capability == PluginCapability.DECLARATIVE:
                return None  # declarative 无须审批
            code_hash = compute_tree_hash(plugin_dir)
            write_approval(plugin_dir, code_hash)
            await self._emit(
                EventType.PLUGIN_APPROVED, {"name": name, "code_hash": code_hash}
            )
            await self._reconcile_locked()
            return self._records.get(name)

    async def toggle(self, name: str, enabled: bool) -> PluginRecord | None:
        """启用/禁用 plugin（.disabled marker）。code plugin 启用仍须已审批+hash 匹配。"""
        async with self._lock:
            plugin_dir = self._plugins_dir / name
            if not (plugin_dir / PLUGIN_MANIFEST_FILE).is_file():
                return None
            marker = plugin_dir / PLUGIN_DISABLED_MARKER
            if enabled:
                try:
                    marker.unlink(missing_ok=True)
                except OSError:
                    pass
            else:
                marker.write_text("", encoding="utf-8")
            await self._emit(
                EventType.PLUGIN_TOGGLED, {"name": name, "enabled": enabled}
            )
            await self._reconcile_locked()
            return self._records.get(name)

    async def remove(self, name: str) -> bool:
        """卸载删除 plugin（仅 plugins_dir 内）。"""
        async with self._lock:
            plugin_dir = self._plugins_dir / name
            resolved = plugin_dir.resolve()
            root = self._plugins_dir.resolve()
            if root not in resolved.parents:
                raise ValueError("path_escape: plugin 目录逃逸 plugins_dir")
            if not plugin_dir.is_dir():
                return False
            shutil.rmtree(plugin_dir, ignore_errors=True)
            await self._emit(EventType.PLUGIN_REMOVED, {"name": name})
            await self._reconcile_locked()
            return True

    async def install(self, repo_url: str) -> PluginRecord | None:
        """git clone 安装 plugin（硬化，FR-7）。code plugin 默认 pending_approval（git 来源不自动信任）。

        Raises:
            GitError: 非法 repo / git 不可用 / 网络失败 / 已存在（route 映射 4xx）。
        """
        # clone 走网络 I/O，**不持锁**（避免阻塞 refresh/toggle）；temp-then-move 对并发 reconcile 安全。
        result = await git_install(repo_url, self._plugins_dir)
        async with self._lock:
            await self._reconcile_locked()
        return self._records.get(result.name)

    async def update(self, name: str) -> PluginRecord | None:
        """git pull 更新已有 git plugin（FR-7.3）。改 code → reconcile 自动转 pending_approval（re-approval）。"""
        async with self._lock:
            plugin_dir = self._plugins_dir / name
            if not is_git_plugin(plugin_dir):
                raise GitError(f"plugin {name!r} 非 git 来源或不存在")
            await git_update(plugin_dir)  # 持锁（in-place pull 需与 reconcile 一致）
            await self._reconcile_locked()
            return self._records.get(name)

    def list_records(self) -> list[PluginRecord]:
        return list(self._records.values())

    def get_record(self, name: str) -> PluginRecord | None:
        return self._records.get(name)

    async def shutdown(self) -> None:
        """卸载所有已加载 plugin 代码（Phase C 另停 watchdog observer）。"""
        async with self._lock:
            self._unload_all_code()

    # ----------------------------------------------------------------- 核心编排

    async def _reconcile_locked(self) -> None:
        """发现 + 注册全部 plugin（假设已持锁）。"""
        try:
            self._plugins_dir.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            log.warning("plugin_dir_mkdir_failed", error=str(exc))
        await self._ensure_audit_task()

        # 干净重建：先卸载上轮代码（避免 stale 工具/模块）
        old_records = dict(self._records)  # PLUGIN_CODE_CHANGED 转换检测用
        self._unload_all_code()

        records: dict[str, PluginRecord] = {}
        skill_dirs: list[tuple[str, Path]] = []
        pending_events: list[tuple[EventType, dict[str, Any]]] = []

        for plugin_dir in iter_plugin_dirs(self._plugins_dir):
            name = plugin_dir.name
            try:
                record, events = self._process_plugin(plugin_dir)
            except PluginValidationError as exc:
                record = PluginRecord(
                    name=name,
                    state=PluginState.REJECTED,
                    capability=classify(plugin_dir),
                    reject_reason=exc.reason,
                    path=str(plugin_dir),
                )
                events = [
                    (
                        EventType.PLUGIN_REJECTED,
                        {"name": name, "reason": exc.reason.value, "detail": exc.detail[:200]},
                    )
                ]
            except Exception as exc:  # 意外错误也隔离（#6）
                log.exception("plugin_process_unexpected_error", plugin=name)
                record = PluginRecord(
                    name=name,
                    state=PluginState.REJECTED,
                    capability=PluginCapability.DECLARATIVE,
                    reject_reason=PluginRejectedReason.UNKNOWN,
                    path=str(plugin_dir),
                )
                events = [
                    (
                        EventType.PLUGIN_REJECTED,
                        {"name": name, "reason": "unknown", "detail": str(exc)[:200]},
                    )
                ]
            records[name] = record
            pending_events.extend(events)
            if record.state == PluginState.ENABLED and record.provides.skills:
                skill_dirs.append((name, plugin_dir / "skills"))

        # 喂 SkillDiscovery（plugin 源最低优先级 + reject-on-collision）
        self._skill_discovery.set_plugin_skill_dirs(skill_dirs)
        self._skill_discovery.refresh()
        for plugin_name, skill_name in self._skill_discovery.pop_plugin_skill_rejections():
            pending_events.append(
                (
                    EventType.PLUGIN_REJECTED,
                    {
                        "name": plugin_name,
                        "reason": PluginRejectedReason.NAME_COLLISION.value,
                        "detail": f"skill:{skill_name}",
                    },
                )
            )

        # PLUGIN_CODE_CHANGED：已审批 enabled code plugin → 转 pending（code_hash 变/审批失效，DP-6）
        for name, rec in records.items():
            old = old_records.get(name)
            if (
                old is not None
                and old.state == PluginState.ENABLED
                and old.capability == PluginCapability.CODE
                and rec.state == PluginState.PENDING_APPROVAL
            ):
                pending_events.append(
                    (
                        EventType.PLUGIN_CODE_CHANGED,
                        {"name": name, "old_code_hash": old.code_hash, "new_code_hash": rec.code_hash},
                    )
                )

        self._records = records
        for event_type, payload in pending_events:
            await self._emit(event_type, payload)

    def _process_plugin(
        self, plugin_dir: Path
    ) -> tuple[PluginRecord, list[tuple[EventType, dict[str, Any]]]]:
        """处理单个 plugin（sync）：校验 → 分类 → 扫描 → 注册。返回 (record, events)。

        Raises:
            PluginValidationError: 校验/扫描失败（caller 隔离降级）。
        """
        validate_no_symlinks(plugin_dir)  # H-1：拒含 symlink 的 plugin（防换码绕 hash）
        manifest = load_manifest(plugin_dir)  # raises PluginValidationError
        validate_provides(plugin_dir, manifest)  # raises
        capability = classify(plugin_dir)
        scanner_skipped = self._scan_declarative(plugin_dir, manifest)  # raises THREAT_FLAGGED

        disabled = (plugin_dir / PLUGIN_DISABLED_MARKER).is_file()
        base = dict(
            name=manifest.name,
            version=manifest.version,
            description=manifest.description,
            capability=capability,
            source="git" if is_git_plugin(plugin_dir) else "local",  # provenance 从 .git 读非 manifest
            provides=manifest.provides,
            scanner_skipped=scanner_skipped,
            path=str(plugin_dir),
        )

        if disabled:
            return PluginRecord(state=PluginState.DISABLED, **base), []

        if capability == PluginCapability.DECLARATIVE:
            record = PluginRecord(state=PluginState.ENABLED, **base)
            return record, [(EventType.PLUGIN_LOADED, self._loaded_payload(record))]

        # code-capable：审批门控（FR-2.1/2.3）
        code_hash = compute_tree_hash(plugin_dir)
        base["code_hash"] = code_hash
        if not is_approved(plugin_dir, code_hash):
            # 未审批/换码 → pending_approval，**绝不 import**
            record = PluginRecord(state=PluginState.PENDING_APPROVAL, **base)
            return record, [(EventType.PLUGIN_LOADED, self._loaded_payload(record))]

        # 已审批 + hash 匹配 → 专用 loader 加载工具 + hooks（importlib，FR-3.2/3.4）
        try:
            loaded = load_plugin_tools(
                manifest.name,
                plugin_dir,
                manifest.provides.tools,
                self._tool_registry,
                load_hooks=manifest.provides.hooks,
            )
        except PluginLoadError as exc:
            # N1：审批后加载失败（冲突/import 错）→ 清审批，下次 reconcile 落 pending_approval
            # 而非每轮 re-exec 已知失败代码（spec §0.3 审批门控首次执行的意图）。
            clear_approval(plugin_dir)
            reason = (
                PluginRejectedReason.NAME_COLLISION
                if exc.collision is not None
                else PluginRejectedReason.IMPORT_ERROR
            )
            raise PluginValidationError(reason, str(exc)) from exc
        self._loaded_code[manifest.name] = loaded
        record = PluginRecord(state=PluginState.ENABLED, **base)
        return record, [(EventType.PLUGIN_LOADED, self._loaded_payload(record))]

    def _scan_declarative(self, plugin_dir: Path, manifest: PluginManifest) -> bool:
        """声明式制品威胁扫描（manifest + SKILL.md + KNOWLEDGE.md）。

        Returns:
            scanner_skipped（scanner 抛异常时 True，fail-open）。

        Raises:
            PluginValidationError(THREAT_FLAGGED): result.blocked（含 oversize degraded BLOCK）。
        """
        contents: list[tuple[str, str]] = []
        try:
            contents.append(("manifest", (plugin_dir / PLUGIN_MANIFEST_FILE).read_text(encoding="utf-8")))
            for skill_name in manifest.provides.skills:
                p = plugin_dir / "skills" / skill_name / "SKILL.md"
                contents.append((f"skill:{skill_name}", p.read_text(encoding="utf-8")))
            for behavior_file in manifest.provides.behavior:
                p = plugin_dir / "behavior" / behavior_file
                contents.append((f"behavior:{behavior_file}", p.read_text(encoding="utf-8")))
        except OSError as exc:
            raise PluginValidationError(PluginRejectedReason.MISSING_ARTIFACT, str(exc)) from exc

        scanner_skipped = False
        for label, content in contents:
            try:
                result = self._scanner.scan_memory(content)
            except Exception as exc:  # scanner 引擎异常 → fail-open（FR-5.3）
                scanner_skipped = True
                log.warning("plugin_threat_scan_skipped", plugin=manifest.name, label=label, error=str(exc))
                continue
            if getattr(result, "blocked", False):
                # 命中（含 oversize degraded BLOCK）→ 拒载（不含原文，#5）
                raise PluginValidationError(
                    PluginRejectedReason.THREAT_FLAGGED,
                    f"{label}:{getattr(result, 'pattern_id', '?')}",
                )
        return scanner_skipped

    # ----------------------------------------------------------------- helpers

    def _unload_all_code(self) -> None:
        for loaded in self._loaded_code.values():
            try:
                unload_plugin_code(loaded, self._tool_registry)
            except Exception:
                log.warning("plugin_unload_failed", plugin=loaded.plugin_name, exc_info=True)
        self._loaded_code = {}

    def _counts(self) -> dict[str, int]:
        loaded = sum(1 for r in self._records.values() if r.state == PluginState.ENABLED)
        rejected = sum(1 for r in self._records.values() if r.state == PluginState.REJECTED)
        pending = sum(1 for r in self._records.values() if r.state == PluginState.PENDING_APPROVAL)
        return {"loaded": loaded, "rejected": rejected, "pending": pending, "total": len(self._records)}

    @staticmethod
    def _loaded_payload(record: PluginRecord) -> dict[str, Any]:
        return {
            "name": record.name,
            "version": record.version,
            "state": record.state.value,
            "capability": record.capability.value,
            "source": record.source,
        }

    async def _ensure_audit_task(self) -> None:
        """确保 _plugin_registry_audit 占位 task 存在（events FK，仿 daily_routine FR-B5）。"""
        if self._audit_task_ready or self._task_store is None:
            return
        try:
            existing = await self._task_store.get_task(PLUGIN_AUDIT_TASK_ID)
            if existing is None:
                from octoagent.core.models.task import RequesterInfo
                from octoagent.core.models.task import Task as TaskModel

                now_utc = datetime.now(UTC)
                await self._task_store.create_task(
                    TaskModel(
                        task_id=PLUGIN_AUDIT_TASK_ID,
                        created_at=now_utc,
                        updated_at=now_utc,
                        status=TaskStatus.SUCCEEDED,
                        title="F106 Plugin Registry 审计占位",
                        requester=RequesterInfo(channel="system", sender_id="plugin_registry"),
                    )
                )
                conn = getattr(self._task_store, "_conn", None)
                if conn is not None and hasattr(conn, "commit"):
                    try:
                        await conn.commit()
                    except Exception:
                        log.warning("plugin_audit_task_commit_failed", exc_info=True)
            self._audit_task_ready = True
        except Exception:
            log.warning("plugin_audit_task_ensure_failed", exc_info=True)

    async def _emit(self, event_type: EventType, payload: dict[str, Any]) -> None:
        """写审计事件（C6 静默降级）。"""
        if self._event_store is None:
            return
        try:
            event = Event(
                event_id=f"plugin-{ULID()}",
                task_id=PLUGIN_AUDIT_TASK_ID,
                task_seq=0,
                ts=datetime.now(UTC),
                type=event_type,
                actor=ActorType.SYSTEM,
                payload=payload,
                trace_id="",
            )
            append_committed = getattr(self._event_store, "append_event_committed", None)
            if append_committed is not None:
                await append_committed(event)
            else:
                await self._event_store.append_event(event)
        except asyncio.CancelledError:
            raise
        except Exception:
            log.warning("plugin_event_append_failed", event_type=event_type.value, exc_info=True)


__all__ = ["PluginRegistry", "PLUGIN_AUDIT_TASK_ID"]
