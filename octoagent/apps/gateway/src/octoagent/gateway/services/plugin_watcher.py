"""F106 Phase C: plugin 目录 watchdog 热重载（spec DP-6 / FR-6）。

**lazy import + 优雅降级（FR-6.4 / #6）**：`watchdog` 不可用 / observer 启动失败 → watcher 禁用，
手动 `POST /refresh` 仍可，gateway 正常。

**行为**：declarative 制品变更 → 自动 `registry.refresh()` 生效；**code/code_hash 变更 → reconcile
自动转 pending_approval**（Phase B 换码闭合已实现：reconcile `_unload_all_code` + hash 不匹配 → pending），
registry emit `PLUGIN_CODE_CHANGED`。**不照搬 Agent Zero 盲目 purge_namespace reload**。

**race 闭合（review H9）**：observer 在后台线程；经 `run_coroutine_threadsafe` 桥到 asyncio loop，
refresh 走 registry `asyncio.Lock`（unload-then-rebuild 原子）。**防 reload loop**：忽略 marker
（`.disabled`/`.approved`）+ `.git`/`__pycache__` 变更 + debounce 合并 + refresh 串行（pending future 时跳过）。
"""

from __future__ import annotations

import asyncio
import threading
from pathlib import Path
from typing import Any

import structlog

log = structlog.get_logger(__name__)

# 忽略的文件名（loader 管理的 marker，写它们不该触发 reload → 防 loop）+ 目录段
_IGNORE_NAMES = frozenset({".disabled", ".approved"})
_IGNORE_DIR_PARTS = frozenset({".git", "__pycache__"})


class PluginWatcher:
    """监听 plugins_dir，debounce 后触发 registry.refresh（thread→loop 桥接）。"""

    def __init__(
        self,
        plugins_dir: Path,
        registry: Any,
        loop: asyncio.AbstractEventLoop,
        *,
        debounce_sec: float = 0.5,
    ) -> None:
        self._plugins_dir = plugins_dir
        self._registry = registry
        self._loop = loop
        self._debounce_sec = debounce_sec
        self._observer: Any | None = None
        self._timer: threading.Timer | None = None
        self._lock = threading.Lock()
        self._refresh_inflight = False
        self._stopped = False

    def start(self) -> bool:
        """启动 observer。watchdog 不可用 / 启动失败 → False（降级，不抛）。"""
        try:
            from watchdog.events import FileSystemEventHandler
            from watchdog.observers import Observer
        except Exception:
            log.warning("plugin_watcher_disabled_no_watchdog")
            return False
        try:
            self._plugins_dir.mkdir(parents=True, exist_ok=True)
            handler = self._make_handler(FileSystemEventHandler)
            observer = Observer()
            observer.schedule(handler, str(self._plugins_dir), recursive=True)
            observer.daemon = True
            observer.start()
            self._observer = observer
            log.info("plugin_watcher_started", plugins_dir=str(self._plugins_dir))
            return True
        except Exception:
            log.warning("plugin_watcher_start_failed", exc_info=True)
            return False

    def _make_handler(self, base_cls: type) -> Any:
        watcher = self

        class _Handler(base_cls):  # type: ignore[misc, valid-type]
            def on_any_event(self, event: Any) -> None:
                watcher._on_event(str(getattr(event, "src_path", "") or ""))

        return _Handler()

    def _is_ignored(self, src_path: str) -> bool:
        if not src_path:
            return True
        p = Path(src_path)
        if p.name in _IGNORE_NAMES:
            return True
        return any(part in _IGNORE_DIR_PARTS for part in p.parts)

    def _on_event(self, src_path: str) -> None:
        if self._stopped or self._is_ignored(src_path):
            return
        self._schedule_refresh()

    def _schedule_refresh(self) -> None:
        """debounce：取消并重启计时器（运行在 observer 线程）。"""
        with self._lock:
            if self._stopped:
                return  # stop 后不再 arm（review L-1：防 stop 后 observer 残余事件触发 refresh）
            if self._timer is not None:
                self._timer.cancel()
            self._timer = threading.Timer(self._debounce_sec, self._trigger_refresh)
            self._timer.daemon = True
            self._timer.start()

    def _trigger_refresh(self) -> None:
        """桥到 asyncio loop 跑 registry.refresh（refresh 内部 asyncio.Lock 串行 + 换码闭合）。"""
        with self._lock:
            if self._stopped or self._refresh_inflight:
                return  # 已停 / 已有 refresh 在跑，跳过
            self._refresh_inflight = True
        try:
            fut = asyncio.run_coroutine_threadsafe(self._registry.refresh(), self._loop)
            fut.result(timeout=30)
        except Exception:
            log.warning("plugin_watcher_refresh_failed", exc_info=True)
        finally:
            with self._lock:
                self._refresh_inflight = False

    def stop(self) -> None:
        with self._lock:
            self._stopped = True
            if self._timer is not None:
                self._timer.cancel()
                self._timer = None
        if self._observer is not None:
            try:
                self._observer.stop()
                self._observer.join(timeout=5)
            except Exception:
                log.warning("plugin_watcher_stop_failed", exc_info=True)
            self._observer = None
