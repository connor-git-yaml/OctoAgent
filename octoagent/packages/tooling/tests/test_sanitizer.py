"""脱敏测试 -- Phase 8 Sanitizer

验证文件路径 $HOME 替换、环境变量值替换、凭证模式替换、
嵌套 dict 递归脱敏、无敏感数据不变。
"""

from __future__ import annotations

import os

from octoagent.tooling.sanitizer import sanitize_for_event


class TestPathSanitization:
    """文件路径脱敏"""

    def test_home_dir_replaced(self) -> None:
        """$HOME 替换为 ~"""
        home = os.path.expanduser("~")
        data = {"path": f"{home}/Documents/secret.txt"}
        result = sanitize_for_event(data)
        assert result["path"] == "~/Documents/secret.txt"

    def test_no_home_dir_unchanged(self) -> None:
        """无 $HOME 路径不变"""
        data = {"path": "/tmp/test.txt"}
        result = sanitize_for_event(data)
        assert result["path"] == "/tmp/test.txt"


class TestCredentialSanitization:
    """凭证模式脱敏"""

    def test_token_redacted(self) -> None:
        """token=* 替换为 [REDACTED]"""
        data = {"auth": "token=abc123def456"}
        result = sanitize_for_event(data)
        assert "[REDACTED]" in result["auth"]
        assert "abc123def456" not in result["auth"]

    def test_password_redacted(self) -> None:
        """password=* 替换为 [REDACTED]"""
        data = {"config": "password=supersecret"}
        result = sanitize_for_event(data)
        assert "[REDACTED]" in result["config"]
        assert "supersecret" not in result["config"]

    def test_secret_redacted(self) -> None:
        """secret=* 替换为 [REDACTED]"""
        data = {"env": "secret=my_secret_value"}
        result = sanitize_for_event(data)
        assert "[REDACTED]" in result["env"]

    def test_key_redacted(self) -> None:
        """key=* 替换为 [REDACTED]"""
        data = {"api": "key=sk-abcdef123456"}
        result = sanitize_for_event(data)
        assert "[REDACTED]" in result["api"]
        assert "sk-abcdef123456" not in result["api"]

    def test_sensitive_key_name_redacted(self) -> None:
        """键名包含 password/secret/token/key 的值被脱敏"""
        data = {
            "api_key": "sk-12345",
            "db_password": "p@ssw0rd",
            "auth_token": "bearer_token_xyz",
            "client_secret": "secret_abc",
        }
        result = sanitize_for_event(data)
        assert result["api_key"] == "[REDACTED]"
        assert result["db_password"] == "[REDACTED]"
        assert result["auth_token"] == "[REDACTED]"
        assert result["client_secret"] == "[REDACTED]"


class TestNestedSanitization:
    """嵌套 dict 递归脱敏"""

    def test_nested_dict(self) -> None:
        """嵌套 dict 递归处理"""
        home = os.path.expanduser("~")
        data = {
            "config": {
                "path": f"{home}/config.yaml",
                "credentials": {
                    "api_key": "sk-test-key",
                },
            }
        }
        result = sanitize_for_event(data)
        assert result["config"]["path"] == "~/config.yaml"
        assert result["config"]["credentials"]["api_key"] == "[REDACTED]"

    def test_list_values(self) -> None:
        """列表中的值也被处理"""
        home = os.path.expanduser("~")
        data = {"paths": [f"{home}/a.txt", f"{home}/b.txt"]}
        result = sanitize_for_event(data)
        assert result["paths"][0] == "~/a.txt"
        assert result["paths"][1] == "~/b.txt"


class TestNoSensitiveData:
    """无敏感数据不变"""

    def test_plain_data_unchanged(self) -> None:
        """普通数据不变"""
        data = {"name": "echo", "count": 42, "enabled": True}
        result = sanitize_for_event(data)
        assert result == data

    def test_empty_dict(self) -> None:
        """空 dict 不变"""
        assert sanitize_for_event({}) == {}

    def test_numeric_values_unchanged(self) -> None:
        """数值类型不变"""
        data = {"duration": 1.5, "count": 10}
        result = sanitize_for_event(data)
        assert result == data
