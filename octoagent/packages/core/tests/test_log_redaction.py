"""F129 FR-E 日志脱敏测试（spec [@test] 绑定：AC-4 / AC-8 / FR-E1~E5）。

Hermetic：纯函数测试，零文件系统 / 零子进程 / 零网络。
"""

from __future__ import annotations

import importlib

import pytest
from octoagent.core import log_redaction
from octoagent.core.log_redaction import (
    _redact_with_flag,
    redact_sensitive_text,
)


class TestVendorPrefixKeys:
    """FR-E2：厂商前缀（OpenAI/Anthropic/DeepSeek/SiliconFlow 均 sk-）。"""

    def test_long_openai_style_key_masked_head_tail(self) -> None:
        key = "sk-abcdef1234567890abcdefXYZ"
        result = redact_sensitive_text(f"calling provider with {key} now")
        assert key not in result
        # 长 token（>=18）留头 6 尾 4
        assert "sk-abc…fXYZ" in result

    def test_anthropic_sk_ant_key_masked(self) -> None:
        key = "sk-ant-api03-abcdefghijklmnopqrstuvwxyz0123456789"
        result = redact_sensitive_text(f"anthropic key {key}")
        assert key not in result
        assert "sk-ant" in result  # 头 6 保留可辨认厂商

    def test_short_key_fully_masked(self) -> None:
        # <18 全遮（FR-E2 掩码策略）
        result = redact_sensitive_text("bad key sk-shortkey1 here")
        assert "sk-shortkey1" not in result
        assert "***" in result


class TestEnvAndJsonFields:
    """FR-E2：通用 ENV/JSON 字段名含 API_KEY|TOKEN|SECRET|PASSWORD|CREDENTIAL|AUTH。"""

    def test_env_assignment_masked(self) -> None:
        result = redact_sensitive_text("SILICONFLOW_API_KEY=abc123def456ghi789jkl")
        assert "abc123def456ghi789jkl" not in result
        assert "SILICONFLOW_API_KEY=" in result

    def test_env_short_value_fully_masked(self) -> None:
        result = redact_sensitive_text("MY_SECRET=hunter2")
        assert "hunter2" not in result
        assert "MY_SECRET=***" in result

    @pytest.mark.parametrize(
        "line",
        [
            "TOKEN=abc123def456ghi789jkl",
            "API_KEY=abc123def456ghi789jkl",
            "PASSWORD=hunter2secret",
        ],
    )
    def test_bare_env_key_names_masked(self, line: str) -> None:
        """Codex review P1（十轮）：裸键名（无前缀）同样是 secret 赋值。"""
        result = redact_sensitive_text(line)
        secret = line.split("=", 1)[1]
        assert secret not in result

    def test_json_field_masked(self) -> None:
        result = redact_sensitive_text('payload {"api_key": "value12345"} sent')
        assert "value12345" not in result
        assert '"api_key"' in result

    def test_password_field_masked(self) -> None:
        result = redact_sensitive_text('{"password": "correct-horse-battery"}')
        assert "correct-horse-battery" not in result


class TestBearerAndTokens:
    """FR-E2：Authorization Bearer / Telegram bot token / JWT / 连接串。"""

    def test_bearer_token_masked(self) -> None:
        result = redact_sensitive_text(
            "Authorization: Bearer abcdefghijklmnop123456789"
        )
        assert "abcdefghijklmnop123456789" not in result
        assert "Bearer " in result

    def test_telegram_bot_token_masked(self) -> None:
        token = "110201543:AAHdqTcvCH1vGWJxfSeofSAs0K5PALDsaw"
        result = redact_sensitive_text(f"telegram init with {token}")
        assert token not in result
        assert "110201543:" in result  # bot id 非 secret，保留可辨认

    def test_jwt_masked(self) -> None:
        jwt = (
            "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9."
            "eyJzdWIiOiIxMjM0NTY3ODkwIn0."
            "SflKxwRJSMeKKF2QT4fwpMeJf36POk6yJVadQssw5c"
        )
        result = redact_sensitive_text(f"token={jwt}")
        assert jwt not in result

    def test_connection_string_password_masked(self) -> None:
        result = redact_sensitive_text(
            "connect postgres://octo:hunter2secret@localhost:5432/octoagent"
        )
        assert "hunter2secret" not in result
        assert "postgres://octo:***@localhost:5432/octoagent" in result

    def test_sse_access_token_query_masked(self) -> None:
        """F134 FR-3c 契约钉住：SSE query 形态 ``?access_token=<urlsafe>``
        必须命中掩码（uvicorn access log 脱敏 filter 依赖本行为——目前经
        ``_ENV_ASSIGN_PATTERN`` 的 TOKEN 字段名命中，pattern 重构不得破坏）。"""
        token = "Xy9_" + "a" * 39  # token_urlsafe(32) 同长（43 字符）
        line = f'127.0.0.1:50000 - "GET /api/stream/task/t1?access_token={token} HTTP/1.1" 200'
        result = redact_sensitive_text(line)
        assert token not in result
        assert "access_token=" in result  # 参数名保留（排障可读）
        assert "/api/stream/task/t1" in result


class TestSafetyProperties:
    """FR-E4：非密钥不误遮 / 幂等；FR-E3：import 快照。"""

    def test_plain_text_untouched(self) -> None:
        text = "task task-123 completed in 4.2s with 3 tool calls"
        assert redact_sensitive_text(text) == text

    def test_url_without_credentials_untouched(self) -> None:
        text = "GET https://api.example.com/v1/models returned 200"
        assert redact_sensitive_text(text) == text

    def test_chinese_log_line_untouched(self) -> None:
        text = "任务已完成，共调用 3 个工具"
        assert redact_sensitive_text(text) == text

    def test_empty_string(self) -> None:
        assert redact_sensitive_text("") == ""

    @pytest.mark.parametrize(
        "sample",
        [
            "sk-abcdef1234567890abcdefXYZ",
            "SILICONFLOW_API_KEY=abc123def456ghi789jkl",
            "Authorization: Bearer abcdefghijklmnop123456789",
            "110201543:AAHdqTcvCH1vGWJxfSeofSAs0K5PALDsaw",
            "postgres://octo:hunter2secret@localhost/db",
        ],
    )
    def test_idempotent(self, sample: str) -> None:
        once = redact_sensitive_text(sample)
        twice = redact_sensitive_text(once)
        assert twice == once

    def test_ansi_color_codes_cannot_shield_secrets(self) -> None:
        """rich/ConsoleRenderer 色码（字母结尾）紧贴 secret 会打断 \\b 词边界
        ——剥 ANSI 降级第二遍必须兜住（实测 rich traceback 泄漏路径）。"""
        secret = "sk-abcdef1234567890abcdefXYZ"
        colored = f'\x1b[33m"\x1b[0m\x1b[33m{secret}\x1b[0m\x1b[33m"\x1b[0m'
        result = redact_sensitive_text(colored)
        assert secret not in result

    def test_plain_colored_line_without_secret_keeps_ansi(self) -> None:
        """无 secret 的彩色行保持原样（不无差别剥色）。"""
        colored = "\x1b[32minfo\x1b[0m task completed"
        assert redact_sensitive_text(colored) == colored

    def test_runtime_env_change_does_not_disable(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """AC-8：运行时 export OCTOAGENT_LOG_REDACT=false 后新日志仍脱敏。"""
        monkeypatch.setenv("OCTOAGENT_LOG_REDACT", "false")
        # 不 reload —— 模拟运行时改 env：import 快照必须仍然生效
        result = redact_sensitive_text("sk-abcdef1234567890abcdefXYZ")
        assert "sk-abcdef1234567890abcdefXYZ" not in result

    def test_disabled_flag_passthrough(self) -> None:
        """内部纯函数分支：显式 disabled 时原样返回（供 reload 语义验证）。"""
        text = "sk-abcdef1234567890abcdefXYZ"
        assert _redact_with_flag(text, enabled=False) == text

    def test_import_snapshot_respects_env_at_import_time(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """FR-E3：快照取的是 import 时 env——env=false 时 reload 后关闭生效。"""
        monkeypatch.setenv("OCTOAGENT_LOG_REDACT", "false")
        try:
            reloaded = importlib.reload(log_redaction)
            text = "sk-abcdef1234567890abcdefXYZ"
            assert reloaded.redact_sensitive_text(text) == text
        finally:
            # 恢复默认 ON 快照，防污染其他测试
            monkeypatch.setenv("OCTOAGENT_LOG_REDACT", "true")
            importlib.reload(log_redaction)

    def test_default_snapshot_is_enabled(self) -> None:
        """默认（未设 env / 非 false 值）必须 ON（FR-E3 安全默认）。"""
        assert log_redaction._REDACT_ENABLED is True
