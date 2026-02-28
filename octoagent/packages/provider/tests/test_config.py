"""ProviderConfig + load_provider_config 单元测试

对齐 tasks.md T013: 验证环境变量映射、默认值。
"""


import pytest
from octoagent.provider.config import ProviderConfig, load_provider_config
from pydantic import SecretStr, ValidationError


class TestProviderConfig:
    """ProviderConfig 数据模型测试"""

    def test_default_values(self):
        """默认值验证"""
        config = ProviderConfig()
        assert config.proxy_base_url == "http://localhost:4000"
        assert config.proxy_api_key.get_secret_value() == ""
        assert config.llm_mode == "litellm"
        assert config.timeout_s == 30

    def test_custom_values(self):
        """自定义值构造"""
        config = ProviderConfig(
            proxy_base_url="http://proxy:8080",
            proxy_api_key=SecretStr("sk-test"),
            llm_mode="echo",
            timeout_s=60,
        )
        assert config.proxy_base_url == "http://proxy:8080"
        assert config.proxy_api_key.get_secret_value() == "sk-test"
        assert config.llm_mode == "echo"
        assert config.timeout_s == 60

    def test_timeout_min_value(self):
        """超时最小值为 1"""
        with pytest.raises(ValidationError):
            ProviderConfig(timeout_s=0)

    def test_timeout_negative_rejected(self):
        """负数超时被拒绝"""
        with pytest.raises(ValidationError):
            ProviderConfig(timeout_s=-1)


class TestLoadProviderConfig:
    """load_provider_config() 环境变量映射测试"""

    def test_default_when_no_env(self, monkeypatch):
        """无环境变量时使用默认值"""
        # 清除相关环境变量
        monkeypatch.delenv("LITELLM_PROXY_URL", raising=False)
        monkeypatch.delenv("LITELLM_PROXY_KEY", raising=False)
        monkeypatch.delenv("OCTOAGENT_LLM_MODE", raising=False)
        monkeypatch.delenv("OCTOAGENT_LLM_TIMEOUT_S", raising=False)

        config = load_provider_config()
        assert config.proxy_base_url == "http://localhost:4000"
        assert config.proxy_api_key.get_secret_value() == ""
        assert config.llm_mode == "litellm"
        assert config.timeout_s == 30

    def test_proxy_url_from_env(self, monkeypatch):
        """LITELLM_PROXY_URL 映射"""
        monkeypatch.setenv("LITELLM_PROXY_URL", "http://proxy:9999")
        monkeypatch.delenv("LITELLM_PROXY_KEY", raising=False)
        monkeypatch.delenv("OCTOAGENT_LLM_MODE", raising=False)
        monkeypatch.delenv("OCTOAGENT_LLM_TIMEOUT_S", raising=False)

        config = load_provider_config()
        assert config.proxy_base_url == "http://proxy:9999"

    def test_proxy_key_from_env(self, monkeypatch):
        """LITELLM_PROXY_KEY 映射"""
        monkeypatch.delenv("LITELLM_PROXY_URL", raising=False)
        monkeypatch.setenv("LITELLM_PROXY_KEY", "sk-secret")
        monkeypatch.delenv("OCTOAGENT_LLM_MODE", raising=False)
        monkeypatch.delenv("OCTOAGENT_LLM_TIMEOUT_S", raising=False)

        config = load_provider_config()
        assert config.proxy_api_key.get_secret_value() == "sk-secret"

    def test_llm_mode_from_env(self, monkeypatch):
        """OCTOAGENT_LLM_MODE 映射"""
        monkeypatch.delenv("LITELLM_PROXY_URL", raising=False)
        monkeypatch.delenv("LITELLM_PROXY_KEY", raising=False)
        monkeypatch.setenv("OCTOAGENT_LLM_MODE", "echo")
        monkeypatch.delenv("OCTOAGENT_LLM_TIMEOUT_S", raising=False)

        config = load_provider_config()
        assert config.llm_mode == "echo"

    def test_timeout_from_env(self, monkeypatch):
        """OCTOAGENT_LLM_TIMEOUT_S 映射"""
        monkeypatch.delenv("LITELLM_PROXY_URL", raising=False)
        monkeypatch.delenv("LITELLM_PROXY_KEY", raising=False)
        monkeypatch.delenv("OCTOAGENT_LLM_MODE", raising=False)
        monkeypatch.setenv("OCTOAGENT_LLM_TIMEOUT_S", "60")

        config = load_provider_config()
        assert config.timeout_s == 60

    def test_invalid_timeout_uses_default(self, monkeypatch):
        """无效 timeout 值不阻塞启动，使用默认值"""
        monkeypatch.delenv("LITELLM_PROXY_URL", raising=False)
        monkeypatch.delenv("LITELLM_PROXY_KEY", raising=False)
        monkeypatch.delenv("OCTOAGENT_LLM_MODE", raising=False)
        monkeypatch.setenv("OCTOAGENT_LLM_TIMEOUT_S", "not-a-number")

        config = load_provider_config()
        assert config.timeout_s == 30  # 使用默认值

    def test_invalid_llm_mode_rejected(self):
        """无效的 llm_mode 值被 Pydantic 拒绝"""
        with pytest.raises(ValidationError):
            ProviderConfig(llm_mode="openai")

    def test_all_env_vars(self, monkeypatch):
        """所有环境变量同时设置"""
        monkeypatch.setenv("LITELLM_PROXY_URL", "http://custom:4000")
        monkeypatch.setenv("LITELLM_PROXY_KEY", "sk-key")
        monkeypatch.setenv("OCTOAGENT_LLM_MODE", "litellm")
        monkeypatch.setenv("OCTOAGENT_LLM_TIMEOUT_S", "45")

        config = load_provider_config()
        assert config.proxy_base_url == "http://custom:4000"
        assert config.proxy_api_key.get_secret_value() == "sk-key"
        assert config.llm_mode == "litellm"
        assert config.timeout_s == 45
