"""F105 Phase A: PlatformRegistry + ChannelAdapter Protocol 测试。

覆盖 spec US-2 AC-1（FakeAdapter 扇出/生命周期）/ AC-2（alias 解析）
+ FR-A2（Protocol fail-fast）/ FR-B1（重复注册）/ FR-B2（异常隔离）
/ FR-B3（startup 序 + shutdown 逆序 + 异常传播）/ FR-B4（alias 冲突）。
"""

from __future__ import annotations

import pytest
from octoagent.gateway.channels import (
    ChannelAdapter,
    ChannelCapabilityMeta,
    PlatformRegistry,
)


class FakeAdapter:
    """符合 ChannelAdapter Protocol 的测试 adapter。"""

    def __init__(
        self,
        platform_id: str,
        *,
        aliases: tuple[str, ...] = (),
        notification_channel_name: str = "",
        notification_channel: object | None = None,
    ) -> None:
        self._meta = ChannelCapabilityMeta(
            platform_id=platform_id,
            label=platform_id.title(),
            aliases=aliases,
            notification_channel_name=notification_channel_name,
        )
        self._notification_channel = notification_channel
        self.notify_calls: list[str] = []
        self.lifecycle_events: list[str] = []
        self.fail_notify: bool = False
        self.fail_startup: bool = False
        # 跨实例共享时序记录（测试注入）
        self.shared_order: list[str] | None = None

    @property
    def meta(self) -> ChannelCapabilityMeta:
        return self._meta

    def notification_channel(self) -> object | None:
        return self._notification_channel

    def inbound_router(self) -> object | None:
        """F105 v0.2 ingress 契约成员（Protocol 演化，additive 补方法）。"""
        return None

    async def notify_task_result(self, task_id: str) -> None:
        if self.fail_notify:
            raise RuntimeError("模拟 notify 失败")
        self.notify_calls.append(task_id)
        if self.shared_order is not None:
            self.shared_order.append(self._meta.platform_id)

    async def startup(self) -> None:
        if self.fail_startup:
            raise RuntimeError("模拟 startup 失败")
        self.lifecycle_events.append("startup")
        if self.shared_order is not None:
            self.shared_order.append(f"up:{self._meta.platform_id}")

    async def shutdown(self) -> None:
        self.lifecycle_events.append("shutdown")
        if self.shared_order is not None:
            self.shared_order.append(f"down:{self._meta.platform_id}")


def test_fake_adapter_satisfies_protocol() -> None:
    """FakeAdapter 满足 runtime_checkable Protocol（FR-A2 前提）。"""
    adapter = FakeAdapter("fake")
    assert isinstance(adapter, ChannelAdapter)


def test_register_rejects_non_adapter() -> None:
    """非 Protocol 对象注册 fail-fast（FR-A2）。"""
    registry = PlatformRegistry()
    with pytest.raises(TypeError):
        registry.register(object())  # type: ignore[arg-type]


def test_register_rejects_duplicate_platform_id() -> None:
    """platform_id 重复注册 raise（FR-B1）。"""
    registry = PlatformRegistry()
    registry.register(FakeAdapter("tg"))
    with pytest.raises(ValueError, match="重复注册"):
        registry.register(FakeAdapter("tg"))


def test_register_rejects_alias_conflict() -> None:
    """alias 命名空间冲突 raise（FR-B4）。"""
    registry = PlatformRegistry()
    registry.register(FakeAdapter("web", aliases=("web_sse",)))
    with pytest.raises(ValueError, match="alias 冲突"):
        registry.register(FakeAdapter("sse", aliases=("web_sse",)))


def test_alias_resolution() -> None:
    """US-2 AC-2：platform_id / aliases / notification_channel_name 全可解析。"""
    registry = PlatformRegistry()
    web = FakeAdapter("web", aliases=("web_sse",))
    tg = FakeAdapter("telegram", notification_channel_name="telegram")
    registry.register(web)
    registry.register(tg)

    assert registry.get("web") is web
    assert registry.get("missing") is None
    assert registry.resolve("web") is web
    assert registry.resolve("web_sse") is web
    assert registry.resolve("telegram") is tg
    assert registry.resolve("unknown") is None


def test_list_adapters_preserves_registration_order() -> None:
    """list_adapters 按注册序（FR-B1，装配序等价的基础）。"""
    registry = PlatformRegistry()
    a, b, c = FakeAdapter("a"), FakeAdapter("b"), FakeAdapter("c")
    for adapter in (a, b, c):
        registry.register(adapter)
    assert registry.list_adapters() == [a, b, c]


async def test_fake_adapter_receives_fanout_and_lifecycle() -> None:
    """US-2 AC-1：注册后自动获得完成回复扇出 + 生命周期（注册序/逆序）。"""
    registry = PlatformRegistry()
    order: list[str] = []
    first = FakeAdapter("first")
    second = FakeAdapter("second")
    first.shared_order = order
    second.shared_order = order
    registry.register(first)
    registry.register(second)

    await registry.notify_task_completion("task-1")
    assert first.notify_calls == ["task-1"]
    assert second.notify_calls == ["task-1"]

    await registry.startup_all()
    await registry.shutdown_all()
    assert order == [
        "first",
        "second",
        "up:first",
        "up:second",
        "down:second",
        "down:first",
    ]


async def test_notify_fanout_isolates_failures() -> None:
    """FR-B2：单 adapter 异常不阻断其余扇出（Constitution #6）。"""
    registry = PlatformRegistry()
    failing = FakeAdapter("failing")
    failing.fail_notify = True
    healthy = FakeAdapter("healthy")
    registry.register(failing)
    registry.register(healthy)

    await registry.notify_task_completion("task-2")
    assert failing.notify_calls == []
    assert healthy.notify_calls == ["task-2"]


async def test_startup_all_propagates_exception() -> None:
    """FR-B3：startup 异常向上传播（baseline harness 直调无守卫，等价）。"""
    registry = PlatformRegistry()
    failing = FakeAdapter("failing")
    failing.fail_startup = True
    registry.register(failing)

    with pytest.raises(RuntimeError, match="模拟 startup 失败"):
        await registry.startup_all()
