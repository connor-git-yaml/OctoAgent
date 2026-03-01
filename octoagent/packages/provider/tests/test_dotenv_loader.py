"""dotenv 加载集成测试 -- T049

覆盖:
- .env 存在时加载成功
- .env 不存在时静默跳过
- 环境变量不被覆盖（override=False）
- 语法错误处理（EC-7）
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from octoagent.provider.dx.dotenv_loader import load_project_dotenv


class TestDotenvLoaderExists:
    """.env 存在场景"""

    def test_load_valid_env(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """有效 .env 文件加载成功"""
        env_file = tmp_path / ".env"
        env_file.write_text(
            "TEST_DOTENV_VAR=hello_world\n",
            encoding="utf-8",
        )
        # 确保测试前该变量不存在
        monkeypatch.delenv("TEST_DOTENV_VAR", raising=False)

        result = load_project_dotenv(project_root=tmp_path)
        assert result is True
        assert os.environ.get("TEST_DOTENV_VAR") == "hello_world"

        # 清理
        monkeypatch.delenv("TEST_DOTENV_VAR", raising=False)

    def test_load_multiple_vars(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """多个环境变量加载"""
        env_file = tmp_path / ".env"
        env_file.write_text(
            "DOTENV_A=value_a\nDOTENV_B=value_b\n",
            encoding="utf-8",
        )
        monkeypatch.delenv("DOTENV_A", raising=False)
        monkeypatch.delenv("DOTENV_B", raising=False)

        result = load_project_dotenv(project_root=tmp_path)
        assert result is True
        assert os.environ.get("DOTENV_A") == "value_a"
        assert os.environ.get("DOTENV_B") == "value_b"

        monkeypatch.delenv("DOTENV_A", raising=False)
        monkeypatch.delenv("DOTENV_B", raising=False)

    def test_load_empty_env(self, tmp_path: Path) -> None:
        """空 .env 文件"""
        env_file = tmp_path / ".env"
        env_file.write_text("", encoding="utf-8")

        # 空文件不会设置任何变量，但不应报错
        result = load_project_dotenv(project_root=tmp_path)
        # python-dotenv 对空文件返回 True（文件存在就算 loaded）
        assert isinstance(result, bool)


class TestDotenvLoaderMissing:
    """.env 不存在场景"""

    def test_missing_env_returns_false(self, tmp_path: Path) -> None:
        """.env 不存在时返回 False"""
        result = load_project_dotenv(project_root=tmp_path)
        assert result is False

    def test_missing_env_no_exception(self, tmp_path: Path) -> None:
        """.env 不存在时不抛异常"""
        # 不应有任何异常
        load_project_dotenv(project_root=tmp_path)


class TestDotenvLoaderNoOverride:
    """环境变量不被覆盖场景"""

    def test_existing_env_not_overridden(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """已设置的环境变量不被 .env 覆盖（override=False）"""
        env_file = tmp_path / ".env"
        env_file.write_text(
            "EXISTING_VAR=from_dotenv\n",
            encoding="utf-8",
        )
        # 预先设置环境变量
        monkeypatch.setenv("EXISTING_VAR", "from_system")

        load_project_dotenv(project_root=tmp_path, override=False)
        # 应保持原值
        assert os.environ.get("EXISTING_VAR") == "from_system"

    def test_override_true_replaces_value(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """override=True 时覆盖已有环境变量"""
        env_file = tmp_path / ".env"
        env_file.write_text(
            "OVERRIDE_VAR=from_dotenv\n",
            encoding="utf-8",
        )
        monkeypatch.setenv("OVERRIDE_VAR", "from_system")

        load_project_dotenv(project_root=tmp_path, override=True)
        assert os.environ.get("OVERRIDE_VAR") == "from_dotenv"

        monkeypatch.delenv("OVERRIDE_VAR", raising=False)

    def test_new_var_always_loaded(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """未设置的环境变量始终从 .env 加载"""
        env_file = tmp_path / ".env"
        env_file.write_text(
            "BRAND_NEW_VAR=from_dotenv\n",
            encoding="utf-8",
        )
        monkeypatch.delenv("BRAND_NEW_VAR", raising=False)

        load_project_dotenv(project_root=tmp_path, override=False)
        assert os.environ.get("BRAND_NEW_VAR") == "from_dotenv"

        monkeypatch.delenv("BRAND_NEW_VAR", raising=False)


class TestDotenvLoaderSyntaxError:
    """语法错误处理（EC-7）"""

    def test_malformed_env_does_not_crash(self, tmp_path: Path) -> None:
        """语法有问题的 .env 不阻塞启动"""
        env_file = tmp_path / ".env"
        # python-dotenv 对大多数格式都很宽容，
        # 但我们测试确保不会抛异常
        env_file.write_text(
            "===INVALID LINE===\nVALID_KEY=value\n",
            encoding="utf-8",
        )
        # 不应抛出异常
        result = load_project_dotenv(project_root=tmp_path)
        assert isinstance(result, bool)

    def test_binary_content_does_not_crash(self, tmp_path: Path) -> None:
        """二进制内容的 .env 不阻塞启动"""
        env_file = tmp_path / ".env"
        env_file.write_bytes(b"\x00\x01\x02\x03SOME_KEY=value\n")
        # 不应抛出异常
        result = load_project_dotenv(project_root=tmp_path)
        assert isinstance(result, bool)


class TestDotenvLoaderDefaultRoot:
    """默认 project_root 行为"""

    def test_default_root_is_cwd(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """project_root=None 时使用 cwd"""
        monkeypatch.chdir(tmp_path)
        # 在 cwd 下没有 .env
        result = load_project_dotenv()
        assert result is False
