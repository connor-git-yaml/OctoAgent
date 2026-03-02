"""LLMService 遗留接口兼容测试。"""

from __future__ import annotations

import importlib
import warnings

from octoagent.gateway.services import llm_service as llm_service_module


def test_internal_legacy_classes_no_subclass_warning() -> None:
    """内部遗留实现不应在模块导入时触发 LLMProvider 子类告警。"""
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always", DeprecationWarning)
        importlib.reload(llm_service_module)

    messages = [str(item.message) for item in caught]
    assert not any("继承自已废弃的 LLMProvider" in message for message in messages)


def test_external_legacy_subclass_still_warns() -> None:
    """外部继续继承遗留基类时仍应收到迁移告警。"""
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always", DeprecationWarning)

        class _CustomLegacyProvider(llm_service_module.LLMProvider):
            async def call(self, prompt: str) -> llm_service_module.LLMResponse:
                raise NotImplementedError

    messages = [str(item.message) for item in caught]
    assert any("继承自已废弃的 LLMProvider" in message for message in messages)
