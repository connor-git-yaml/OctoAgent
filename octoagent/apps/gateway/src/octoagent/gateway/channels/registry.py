"""F105: PlatformRegistry —— 渠道 adapter 注册/解析/扇出/生命周期（spec FR-B）。

harness 装配时构造并注册全部 adapter；之后：

- 通知渠道注册：遍历 ``list_adapters()`` 对非 None ``notification_channel()``
  按注册序注册到 NotificationService（保 baseline 装配序）。
- 任务完成回复：``notify_task_completion(task_id)`` 替代 baseline 硬编码的
  ``telegram_service.notify_task_result``（spec D4 扇出等价论证见 plan C-3）。
- 生命周期：``startup_all()`` 注册序 / ``shutdown_all()`` 逆序。
"""

from __future__ import annotations

import structlog

from .adapter import ChannelAdapter

log = structlog.get_logger()


class PlatformRegistry:
    """渠道 adapter 中央注册表（spec FR-B1~B4）。"""

    def __init__(self) -> None:
        self._adapters: dict[str, ChannelAdapter] = {}
        # alias 解析空间：platform_id ∪ aliases ∪ notification_channel_name
        self._alias_index: dict[str, str] = {}

    def register(self, adapter: ChannelAdapter) -> None:
        """注册 adapter（FR-B1/FR-A2 fail-fast）。

        - 非 ChannelAdapter Protocol 对象 → TypeError
        - platform_id 重复 → ValueError
        - alias 命名空间冲突 → ValueError（FR-B4）
        """
        if not isinstance(adapter, ChannelAdapter):
            raise TypeError(
                f"register 需要 ChannelAdapter Protocol 实现，得到 {type(adapter)!r}"
            )
        meta = adapter.meta
        platform_id = meta.platform_id
        if platform_id in self._adapters:
            raise ValueError(f"platform_id 重复注册: {platform_id}")

        alias_candidates = {platform_id, *meta.aliases}
        if meta.notification_channel_name:
            alias_candidates.add(meta.notification_channel_name)
        for alias in alias_candidates:
            owner = self._alias_index.get(alias)
            if owner is not None and owner != platform_id:
                raise ValueError(
                    f"alias 冲突: {alias!r} 已被 platform {owner!r} 占用"
                )

        self._adapters[platform_id] = adapter
        for alias in alias_candidates:
            self._alias_index[alias] = platform_id
        log.info(
            "platform_adapter_registered",
            platform_id=platform_id,
            aliases=sorted(alias_candidates - {platform_id}),
            total=len(self._adapters),
        )

    def get(self, platform_id: str) -> ChannelAdapter | None:
        """按 platform_id 精确查询。"""
        return self._adapters.get(platform_id)

    def resolve(self, alias: str) -> ChannelAdapter | None:
        """按 alias 解析（platform_id ∪ aliases ∪ notification_channel_name）。"""
        platform_id = self._alias_index.get(alias)
        if platform_id is None:
            return None
        return self._adapters.get(platform_id)

    def list_adapters(self) -> list[ChannelAdapter]:
        """注册序枚举（dict 保插入序）。"""
        return list(self._adapters.values())

    async def notify_task_completion(self, task_id: str) -> None:
        """任务完成回复扇出（FR-B2，spec D4）。

        按注册序逐 adapter 调 ``notify_task_result``；单 adapter 异常记录
        warning 后继续（Constitution #6——通知链路异常不影响其他渠道）。
        """
        for adapter in self._adapters.values():
            try:
                await adapter.notify_task_result(task_id)
            except Exception:
                log.warning(
                    "platform_completion_notify_failed",
                    platform_id=adapter.meta.platform_id,
                    task_id=task_id,
                    exc_info=True,
                )

    async def startup_all(self) -> None:
        """按注册序启动全部 adapter（FR-B3）。

        异常向上传播——baseline 中 harness 对 ``telegram_service.startup()``
        是无守卫直调（octo_harness L1147），吞异常会改变启动失败语义，
        违反行为零变更。v0.2 出现多个真实 startup 的 adapter 时再评估
        per-adapter 隔离（届时是显式行为决策，非顺手改）。
        """
        for adapter in self._adapters.values():
            await adapter.startup()

    async def shutdown_all(self) -> None:
        """按注册逆序停止全部 adapter（FR-B3）。

        异常传播语义同 ``startup_all``（baseline shutdown 直调无守卫）。
        """
        for adapter in reversed(list(self._adapters.values())):
            await adapter.shutdown()
