"""CostTracker -- 成本计算

对齐 contracts/provider-api.md SS4。
双通道策略: completion_cost() -> _hidden_params -> (0.0, True)。
所有方法不抛异常。
"""

import contextlib

import structlog

from .models import TokenUsage

log = structlog.get_logger()

# 隔离 litellm 导入，方便 Mock
try:
    from litellm import completion_cost as litellm_completion_cost
except ImportError:  # pragma: no cover
    litellm_completion_cost = None  # type: ignore[assignment]


class CostTracker:
    """成本追踪器

    提供 LLM 调用成本计算、Token 解析、模型信息提取等静态方法。
    所有方法均不抛出异常，内部捕获所有错误。
    """

    @staticmethod
    def calculate_cost(response) -> tuple[float, bool]:
        """从 LiteLLM 响应计算 USD 成本

        双通道策略：
        1. 主路径: litellm.completion_cost(completion_response=response)
        2. 兜底路径: response._hidden_params.get("response_cost", 0.0)
        3. 全失败: (0.0, True)

        Args:
            response: LiteLLM ModelResponse 对象

        Returns:
            (cost_usd, cost_unavailable) 元组
        """
        # 主路径: litellm.completion_cost()
        try:
            if litellm_completion_cost is not None:
                cost = litellm_completion_cost(completion_response=response)
                if cost is not None and cost >= 0:
                    return float(cost), False
        except Exception as e:
            log.debug("completion_cost_failed", error=str(e))

        # 兜底路径: _hidden_params.response_cost
        try:
            hidden = getattr(response, "_hidden_params", None)
            if hidden and isinstance(hidden, dict):
                cost = hidden.get("response_cost")
                if cost is not None and cost >= 0:
                    return float(cost), False
        except Exception as e:
            log.debug("hidden_params_cost_failed", error=str(e))

        # 双通道均失败
        log.warning("cost_unavailable")
        return 0.0, True

    @staticmethod
    def parse_usage(response) -> TokenUsage:
        """从 LiteLLM 响应解析 token 使用数据

        Args:
            response: LiteLLM ModelResponse 对象

        Returns:
            TokenUsage 实例（失败时返回全零）
        """
        try:
            usage = getattr(response, "usage", None)
            if usage is not None:
                return TokenUsage(
                    prompt_tokens=getattr(usage, "prompt_tokens", 0) or 0,
                    completion_tokens=getattr(usage, "completion_tokens", 0) or 0,
                    total_tokens=getattr(usage, "total_tokens", 0) or 0,
                )
        except Exception as e:
            log.debug("parse_usage_failed", error=str(e))

        return TokenUsage()

    @staticmethod
    def extract_model_info(response) -> tuple[str, str]:
        """从 LiteLLM 响应提取模型和 provider 信息

        Args:
            response: LiteLLM ModelResponse 对象

        Returns:
            (model_name, provider) 元组
        """
        model_name = ""
        provider = ""

        with contextlib.suppress(Exception):
            model_name = getattr(response, "model", "") or ""

        with contextlib.suppress(Exception):
            hidden = getattr(response, "_hidden_params", None)
            if hidden and isinstance(hidden, dict):
                provider = hidden.get("custom_llm_provider", "") or ""

        return model_name, provider
