"""脱敏函数单元测试 -- T014

覆盖: 长字符串 / 短字符串 / 边界情况
"""

from octoagent.provider.auth.masking import mask_secret


class TestMaskSecret:
    """mask_secret 脱敏行为"""

    def test_long_string(self) -> None:
        """长字符串保留前缀和后缀"""
        result = mask_secret("sk-or-v1-abc123xyz789")
        # 前 10 字符 + *** + 后 3 字符
        assert result == "sk-or-v1-a***789"

    def test_anthropic_setup_token(self) -> None:
        """Anthropic Setup Token 脱敏"""
        result = mask_secret("sk-ant-oat01-longtoken123")
        assert result == "sk-ant-oat***123"

    def test_short_string(self) -> None:
        """短字符串（长度 <= 13）返回 ***"""
        assert mask_secret("short") == "***"
        assert mask_secret("1234567890123") == "***"  # 恰好等于 10+3

    def test_exact_boundary(self) -> None:
        """恰好 14 个字符（prefix_len + suffix_len + 1）"""
        result = mask_secret("12345678901234")
        assert result == "1234567890***234"

    def test_empty_string(self) -> None:
        """空字符串"""
        assert mask_secret("") == "***"

    def test_custom_prefix_suffix(self) -> None:
        """自定义前缀/后缀长度"""
        result = mask_secret("abcdefghij", prefix_len=3, suffix_len=2)
        assert result == "abc***ij"

    def test_custom_short(self) -> None:
        """自定义参数下的短字符串"""
        result = mask_secret("abcde", prefix_len=3, suffix_len=3)
        assert result == "***"
