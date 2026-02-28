"""CostTracker 单元测试

对齐 tasks.md T015: 验证 calculate_cost() 双通道策略、parse_usage()、
extract_model_info()、cost_unavailable 标记。
"""

from unittest.mock import MagicMock, patch

from octoagent.provider.cost import CostTracker
from octoagent.provider.models import TokenUsage


def _make_mock_response(
    model: str = "gpt-4o-mini",
    prompt_tokens: int = 10,
    completion_tokens: int = 20,
    total_tokens: int = 30,
    hidden_params: dict | None = None,
):
    """构造 Mock LiteLLM ModelResponse"""
    response = MagicMock()
    response.model = model

    # usage 对象
    usage = MagicMock()
    usage.prompt_tokens = prompt_tokens
    usage.completion_tokens = completion_tokens
    usage.total_tokens = total_tokens
    response.usage = usage

    # _hidden_params
    if hidden_params is None:
        hidden_params = {"response_cost": 0.001}
    response._hidden_params = hidden_params

    return response


class TestCostTrackerCalculateCost:
    """calculate_cost() 双通道策略测试"""

    @patch("octoagent.provider.cost.litellm_completion_cost")
    def test_primary_channel_success(self, mock_cost):
        """主路径: litellm.completion_cost 成功"""
        mock_cost.return_value = 0.005
        response = _make_mock_response()

        cost_usd, cost_unavailable = CostTracker.calculate_cost(response)
        assert cost_usd == 0.005
        assert cost_unavailable is False
        mock_cost.assert_called_once()

    @patch("octoagent.provider.cost.litellm_completion_cost")
    def test_fallback_to_hidden_params(self, mock_cost):
        """兜底路径: completion_cost 失败，从 _hidden_params 获取"""
        mock_cost.side_effect = Exception("pricing not found")
        response = _make_mock_response(hidden_params={"response_cost": 0.002})

        cost_usd, cost_unavailable = CostTracker.calculate_cost(response)
        assert cost_usd == 0.002
        assert cost_unavailable is False

    @patch("octoagent.provider.cost.litellm_completion_cost")
    def test_both_channels_fail(self, mock_cost):
        """双通道均失败: cost_unavailable=True"""
        mock_cost.side_effect = Exception("pricing not found")
        response = _make_mock_response(hidden_params={})

        cost_usd, cost_unavailable = CostTracker.calculate_cost(response)
        assert cost_usd == 0.0
        assert cost_unavailable is True

    @patch("octoagent.provider.cost.litellm_completion_cost")
    def test_no_exception_on_any_failure(self, mock_cost):
        """无论什么错误都不抛出异常"""
        mock_cost.side_effect = RuntimeError("unexpected")
        response = MagicMock()
        response._hidden_params = None

        cost_usd, cost_unavailable = CostTracker.calculate_cost(response)
        assert cost_usd == 0.0
        assert cost_unavailable is True


class TestCostTrackerParseUsage:
    """parse_usage() Token 解析测试"""

    def test_normal_usage(self):
        """正常 usage 数据解析"""
        response = _make_mock_response(
            prompt_tokens=100, completion_tokens=200, total_tokens=300
        )
        usage = CostTracker.parse_usage(response)
        assert isinstance(usage, TokenUsage)
        assert usage.prompt_tokens == 100
        assert usage.completion_tokens == 200
        assert usage.total_tokens == 300

    def test_no_usage(self):
        """无 usage 时返回全零"""
        response = MagicMock()
        response.usage = None

        usage = CostTracker.parse_usage(response)
        assert usage.prompt_tokens == 0
        assert usage.completion_tokens == 0
        assert usage.total_tokens == 0

    def test_usage_attribute_error(self):
        """usage 属性缺失时不抛异常"""
        response = MagicMock(spec=[])  # 无 usage 属性

        usage = CostTracker.parse_usage(response)
        assert usage.total_tokens == 0


class TestCostTrackerExtractModelInfo:
    """extract_model_info() 模型信息提取测试"""

    def test_normal_extraction(self):
        """正常提取模型和 provider"""
        response = _make_mock_response(model="gpt-4o-mini")
        response._hidden_params = {"custom_llm_provider": "openai"}

        model_name, provider = CostTracker.extract_model_info(response)
        assert model_name == "gpt-4o-mini"
        assert provider == "openai"

    def test_no_model(self):
        """无 model 属性时返回空字符串"""
        response = MagicMock(spec=[])
        model_name, provider = CostTracker.extract_model_info(response)
        assert model_name == ""
        assert provider == ""

    def test_no_hidden_params(self):
        """无 _hidden_params 时 provider 为空"""
        response = MagicMock()
        response.model = "claude-3-5-haiku"
        response._hidden_params = {}

        model_name, provider = CostTracker.extract_model_info(response)
        assert model_name == "claude-3-5-haiku"
        assert provider == ""
