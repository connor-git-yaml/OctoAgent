"""PKCE 生成器单元测试 -- T008

验证:
- verifier 长度 43 字符（secrets.token_urlsafe(32) 输出）
- challenge 为正确的 S256 哈希
- state 独立生成且每次不同
对齐 FR-001
"""

from __future__ import annotations

import base64
import hashlib

from octoagent.provider.auth.pkce import PkcePair, generate_pkce, generate_state


class TestPkcePair:
    """PkcePair dataclass 基本属性"""

    def test_frozen(self) -> None:
        """PkcePair 是 frozen dataclass，不可修改"""
        pair = PkcePair(code_verifier="v", code_challenge="c")
        try:
            pair.code_verifier = "new"  # type: ignore[misc]
            assert False, "应该抛出 FrozenInstanceError"
        except AttributeError:
            pass

    def test_slots(self) -> None:
        """PkcePair 使用 slots"""
        pair = PkcePair(code_verifier="v", code_challenge="c")
        assert not hasattr(pair, "__dict__")


class TestGeneratePkce:
    """generate_pkce() 功能验证"""

    def test_verifier_length(self) -> None:
        """verifier 长度为 43 字符（secrets.token_urlsafe(32) 输出）"""
        pair = generate_pkce()
        assert len(pair.code_verifier) == 43

    def test_verifier_is_url_safe(self) -> None:
        """verifier 仅包含 URL-safe 字符"""
        pair = generate_pkce()
        allowed = set("ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_")
        assert all(c in allowed for c in pair.code_verifier)

    def test_challenge_is_s256(self) -> None:
        """challenge 是 verifier 的 SHA256 -> base64url 编码（无 padding）"""
        pair = generate_pkce()
        # 独立计算 challenge
        expected = (
            base64.urlsafe_b64encode(
                hashlib.sha256(pair.code_verifier.encode("ascii")).digest()
            )
            .rstrip(b"=")
            .decode("ascii")
        )
        assert pair.code_challenge == expected

    def test_challenge_no_padding(self) -> None:
        """challenge 不含 base64 padding 字符 '='"""
        pair = generate_pkce()
        assert "=" not in pair.code_challenge

    def test_each_call_generates_unique_pair(self) -> None:
        """每次调用生成不同的 verifier 和 challenge"""
        pairs = [generate_pkce() for _ in range(10)]
        verifiers = {p.code_verifier for p in pairs}
        challenges = {p.code_challenge for p in pairs}
        assert len(verifiers) == 10
        assert len(challenges) == 10

    def test_returns_pkce_pair(self) -> None:
        """返回 PkcePair 实例"""
        pair = generate_pkce()
        assert isinstance(pair, PkcePair)


class TestGenerateState:
    """generate_state() 功能验证"""

    def test_state_length(self) -> None:
        """state 长度为 43 字符（secrets.token_urlsafe(32) 输出）"""
        state = generate_state()
        assert len(state) == 43

    def test_state_is_url_safe(self) -> None:
        """state 仅包含 URL-safe 字符"""
        state = generate_state()
        allowed = set("ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_")
        assert all(c in allowed for c in state)

    def test_state_independent_from_verifier(self) -> None:
        """state 与 verifier 独立生成"""
        pair = generate_pkce()
        state = generate_state()
        assert state != pair.code_verifier

    def test_each_call_generates_unique_state(self) -> None:
        """每次调用生成不同的 state"""
        states = {generate_state() for _ in range(10)}
        assert len(states) == 10
