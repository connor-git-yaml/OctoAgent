"""校验函数单元测试 -- T015

覆盖: 各 Provider 格式校验 / Setup Token 前缀校验 / 空值拒绝
"""

from octoagent.provider.auth.validators import validate_api_key, validate_setup_token


class TestValidateApiKey:
    """API Key 格式校验"""

    def test_openai_valid(self) -> None:
        assert validate_api_key("sk-proj-abc123", "openai") is True

    def test_openai_invalid(self) -> None:
        assert validate_api_key("invalid-key", "openai") is False

    def test_openrouter_valid(self) -> None:
        assert validate_api_key("sk-or-v1-abc", "openrouter") is True

    def test_openrouter_invalid(self) -> None:
        assert validate_api_key("sk-abc", "openrouter") is False

    def test_anthropic_valid(self) -> None:
        assert validate_api_key("sk-ant-api03-xxx", "anthropic") is True

    def test_anthropic_invalid(self) -> None:
        assert validate_api_key("sk-ant-oat01-xxx", "anthropic") is False

    def test_unknown_provider_nonempty(self) -> None:
        """未知 Provider 只要非空就通过"""
        assert validate_api_key("any-key", "unknown-provider") is True

    def test_empty_string(self) -> None:
        assert validate_api_key("", "openai") is False

    def test_whitespace_only(self) -> None:
        assert validate_api_key("   ", "openai") is False

    def test_case_insensitive_provider(self) -> None:
        """Provider 名称大小写不敏感"""
        assert validate_api_key("sk-abc", "OpenAI") is True


class TestValidateSetupToken:
    """Setup Token 格式校验"""

    def test_valid_token(self) -> None:
        assert validate_setup_token("sk-ant-oat01-abc123xyz") is True

    def test_invalid_prefix(self) -> None:
        assert validate_setup_token("sk-ant-api03-xxx") is False

    def test_empty_string(self) -> None:
        assert validate_setup_token("") is False

    def test_whitespace_only(self) -> None:
        assert validate_setup_token("   ") is False

    def test_partial_prefix(self) -> None:
        assert validate_setup_token("sk-ant-oat01") is False

    def test_exact_prefix(self) -> None:
        """恰好是前缀，无后续内容"""
        assert validate_setup_token("sk-ant-oat01-") is True
