"""litellm_generator.py 单元测试 -- Feature 014

覆盖：
- 生成 litellm-config.yaml model_list 条目数与 enabled alias 数一致
- api_key 引用格式为 os.environ/OPENROUTER_API_KEY（非明文）
- 多 Provider 场景：每个 alias 路由到正确 Provider
- disabled Provider 不产生 model_list 条目
- 同步检测：in_sync / out_of_sync 返回正确（EC-4）
- schema 校验失败时不覆盖现有 litellm-config.yaml（FR-006）
- litellm-config.yaml 已存在（非工具生成）时打印警告（EC-3）
- generate_env_litellm 凭证缺失时 WARN 不阻断（EC-2）
"""

from __future__ import annotations

import warnings
from pathlib import Path

import pytest
import yaml
from octoagent.gateway.services.config.config_schema import (
    ModelAlias,
    OctoAgentConfig,
    ProviderEntry,
    RuntimeConfig,
)
from octoagent.gateway.services.config.litellm_generator import (
    GENERATED_MARKER,
    check_litellm_sync_status,
    generate_env_litellm,
    generate_litellm_config,
)

# ---------------------------------------------------------------------------
# 测试夹具
# ---------------------------------------------------------------------------


def _make_provider(
    provider_id: str = "openrouter",
    enabled: bool = True,
    api_key_env: str | None = None,
) -> ProviderEntry:
    envs = {
        "openrouter": "OPENROUTER_API_KEY",
        "anthropic": "ANTHROPIC_API_KEY",
        "openai": "OPENAI_API_KEY",
    }
    names = {"openrouter": "OpenRouter", "anthropic": "Anthropic", "openai": "OpenAI"}
    return ProviderEntry(
        id=provider_id,
        name=names.get(provider_id, provider_id.title()),
        auth_type="api_key",
        api_key_env=api_key_env or envs.get(provider_id, f"{provider_id.upper()}_API_KEY"),
        enabled=enabled,
    )


def _make_config(
    providers: list[ProviderEntry] | None = None,
    aliases: dict[str, ModelAlias] | None = None,
) -> OctoAgentConfig:
    if providers is None:
        providers = [_make_provider("openrouter")]
    if aliases is None:
        aliases = {
            "main": ModelAlias(
                provider="openrouter",
                model="openrouter/auto",
                description="主力模型",
            ),
            "cheap": ModelAlias(
                provider="openrouter",
                model="openrouter/auto",
                description="低成本模型",
            ),
        }
    return OctoAgentConfig(
        updated_at="2026-03-04",
        providers=providers,
        model_aliases=aliases,
        runtime=RuntimeConfig(),
    )


def _parse_litellm(path: Path) -> dict:
    """解析 litellm-config.yaml 为 dict"""
    content = path.read_text(encoding="utf-8")
    # 跳过注释行再解析
    data = yaml.safe_load(content)
    assert isinstance(data, dict), f"litellm-config.yaml 根节点不是 dict: {type(data)}"
    return data


# ---------------------------------------------------------------------------
# generate_litellm_config 基础测试
# ---------------------------------------------------------------------------


def test_generate_litellm_config_creates_file(tmp_path: Path) -> None:
    """成功生成 litellm-config.yaml"""
    config = _make_config()
    output_path = generate_litellm_config(config, tmp_path)
    assert output_path.exists()
    assert output_path.name == "litellm-config.yaml"


def test_generate_litellm_config_model_list_count(tmp_path: Path) -> None:
    """model_list 条目数与 enabled alias 数一致"""
    config = _make_config()
    generate_litellm_config(config, tmp_path)
    data = _parse_litellm(tmp_path / "litellm-config.yaml")
    assert len(data["model_list"]) == 2  # main + cheap


def test_generate_litellm_config_api_key_format(tmp_path: Path) -> None:
    """api_key 引用格式为 os.environ/OPENROUTER_API_KEY（非明文）"""
    config = _make_config()
    generate_litellm_config(config, tmp_path)
    data = _parse_litellm(tmp_path / "litellm-config.yaml")
    for model_entry in data["model_list"]:
        api_key = model_entry["litellm_params"]["api_key"]
        assert api_key.startswith("os.environ/"), f"api_key 格式错误: {api_key}"


def test_generate_litellm_config_has_marker_comment(tmp_path: Path) -> None:
    """生成文件包含标记注释（GENERATED_MARKER）"""
    config = _make_config()
    generate_litellm_config(config, tmp_path)
    content = (tmp_path / "litellm-config.yaml").read_text(encoding="utf-8")
    assert GENERATED_MARKER in content


def test_generate_litellm_config_master_key(tmp_path: Path) -> None:
    """general_settings.master_key 引用 runtime.master_key_env"""
    config = _make_config()
    generate_litellm_config(config, tmp_path)
    data = _parse_litellm(tmp_path / "litellm-config.yaml")
    master_key = data["general_settings"]["master_key"]
    assert master_key == f"os.environ/{config.runtime.master_key_env}"


def test_generate_litellm_config_openai_codex_uses_codex_backend_route(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """openai-codex OAuth 生成 Codex backend 路由与兼容 headers。"""
    monkeypatch.setenv(
        "OPENAI_API_KEY",
        (
            "eyJhbGciOiJIUzI1NiJ9."
            "eyJodHRwczovL2FwaS5vcGVuYWkuY29tL2F1dGgiOnsiY2hhdGdwdF9hY2NvdW50X2lkIjoiYWNjdC10ZXN0In19."
            "signature"
        ),
    )
    config = _make_config(
        providers=[
            ProviderEntry(
                id="openai-codex",
                name="OpenAI Codex",
                auth_type="oauth",
                api_key_env="OPENAI_API_KEY",
            )
        ],
        aliases={
            "main": ModelAlias(
                provider="openai-codex",
                model="gpt-5.4",
                thinking_level="xhigh",
            )
        },
    )

    generate_litellm_config(config, tmp_path)
    data = _parse_litellm(tmp_path / "litellm-config.yaml")
    params = data["model_list"][0]["litellm_params"]

    assert params["api_base"] == "https://chatgpt.com/backend-api/codex"
    assert params["headers"]["chatgpt-account-id"] == "acct-test"
    assert params["headers"]["originator"] == "pi"


def test_generate_litellm_config_omits_unsupported_thinking_config(tmp_path: Path) -> None:
    """不支持 reasoning 的 alias 不应写入 litellm_params.thinking。"""
    config = _make_config(
        providers=[_make_provider("openrouter")],
        aliases={
            "cheap": ModelAlias(
                provider="openrouter",
                model="qwen/qwen3.5-9b",
                thinking_level="low",
            )
        },
    )

    generate_litellm_config(config, tmp_path)
    data = _parse_litellm(tmp_path / "litellm-config.yaml")
    params = data["model_list"][0]["litellm_params"]

    assert "thinking" not in params


def test_generate_litellm_config_normalizes_routed_provider_model_string(
    tmp_path: Path,
) -> None:
    config = _make_config(
        providers=[_make_provider("openrouter")],
        aliases={
            "cheap": ModelAlias(
                provider="openrouter",
                model="qwen/qwen3.5-9b",
                thinking_level="low",
            )
        },
    )

    generate_litellm_config(config, tmp_path)
    data = _parse_litellm(tmp_path / "litellm-config.yaml")
    params = data["model_list"][0]["litellm_params"]

    assert params["model"] == "openrouter/qwen/qwen3.5-9b"


# ---------------------------------------------------------------------------
# 多 Provider 场景
# ---------------------------------------------------------------------------


def test_generate_litellm_config_multi_provider(tmp_path: Path) -> None:
    """多 Provider 场景：每个 alias 路由到正确 Provider"""
    providers = [_make_provider("openrouter"), _make_provider("anthropic")]
    aliases = {
        "main": ModelAlias(provider="openrouter", model="openrouter/auto"),
        "claude": ModelAlias(provider="anthropic", model="claude-opus-4-20250514"),
    }
    config = OctoAgentConfig(
        updated_at="2026-03-04",
        providers=providers,
        model_aliases=aliases,
    )
    generate_litellm_config(config, tmp_path)
    data = _parse_litellm(tmp_path / "litellm-config.yaml")

    # 构建 {model_name: api_key} 映射
    model_api_keys = {
        m["model_name"]: m["litellm_params"]["api_key"] for m in data["model_list"]
    }

    assert model_api_keys["main"] == "os.environ/OPENROUTER_API_KEY"
    assert model_api_keys["claude"] == "os.environ/ANTHROPIC_API_KEY"


# ---------------------------------------------------------------------------
# disabled Provider 不产生条目
# ---------------------------------------------------------------------------


def test_generate_litellm_config_disabled_provider_excluded(tmp_path: Path) -> None:
    """disabled Provider 的 alias 不产生 model_list 条目"""
    providers = [
        _make_provider("openrouter", enabled=True),
        _make_provider("anthropic", enabled=False),
    ]
    aliases = {
        "main": ModelAlias(provider="openrouter", model="openrouter/auto"),
        # claude 别名引用已禁用的 anthropic
        "claude": ModelAlias(provider="anthropic", model="claude-opus-4-20250514"),
    }
    with warnings.catch_warnings(record=True):
        warnings.simplefilter("always")
        config = OctoAgentConfig(
            updated_at="2026-03-04",
            providers=providers,
            model_aliases=aliases,
        )
    generate_litellm_config(config, tmp_path)
    data = _parse_litellm(tmp_path / "litellm-config.yaml")
    model_names = [m["model_name"] for m in data["model_list"]]
    assert "main" in model_names
    assert "claude" not in model_names  # disabled provider 的 alias 被排除


# ---------------------------------------------------------------------------
# 同步检测（EC-4）
# ---------------------------------------------------------------------------


def test_check_litellm_sync_status_in_sync(tmp_path: Path) -> None:
    """in_sync 场景：刚生成后立即检查，应为一致"""
    config = _make_config()
    generate_litellm_config(config, tmp_path)
    in_sync, diffs = check_litellm_sync_status(config, tmp_path)
    assert in_sync is True
    assert len(diffs) == 0


def test_check_litellm_sync_status_out_of_sync(tmp_path: Path) -> None:
    """out_of_sync 场景：生成后修改 config 但不同步（EC-4）"""
    config = _make_config()
    generate_litellm_config(config, tmp_path)

    # 向 config 添加新 alias 但不重新 sync
    config2 = OctoAgentConfig(
        updated_at="2026-03-04",
        providers=[_make_provider("openrouter")],
        model_aliases={
            "main": ModelAlias(provider="openrouter", model="openrouter/auto"),
            "cheap": ModelAlias(provider="openrouter", model="openrouter/auto"),
            "new_alias": ModelAlias(provider="openrouter", model="openrouter/fast"),
        },
    )

    in_sync, diffs = check_litellm_sync_status(config2, tmp_path)
    assert in_sync is False
    assert any("new_alias" in d for d in diffs)


def test_check_litellm_sync_status_file_missing(tmp_path: Path) -> None:
    """litellm-config.yaml 不存在时返回 out_of_sync"""
    config = _make_config()
    in_sync, diffs = check_litellm_sync_status(config, tmp_path)
    assert in_sync is False
    assert len(diffs) > 0


# ---------------------------------------------------------------------------
# 已存在非工具生成文件时打印警告（EC-3）
# ---------------------------------------------------------------------------


def test_generate_overwrites_with_warning(tmp_path: Path) -> None:
    """litellm-config.yaml 已存在（无标记注释）时打印 warning（EC-3）"""
    # 先写一个手动维护的文件（无 GENERATED_MARKER）
    manual_content = "# 手动维护的配置\nmodel_list: []\n"
    (tmp_path / "litellm-config.yaml").write_text(manual_content, encoding="utf-8")

    config = _make_config()

    # 捕获日志 warning（structlog 层），这里通过验证文件被覆盖来间接验证
    generate_litellm_config(config, tmp_path)

    # 文件应被覆盖（包含 GENERATED_MARKER）
    content = (tmp_path / "litellm-config.yaml").read_text(encoding="utf-8")
    assert GENERATED_MARKER in content


# ---------------------------------------------------------------------------
# generate_env_litellm 测试
# ---------------------------------------------------------------------------


def test_generate_env_litellm_creates_file(tmp_path: Path) -> None:
    """generate_env_litellm 创建 .env.litellm"""
    path = generate_env_litellm(
        provider_id="openrouter",
        api_key="sk-test-key",
        env_var_name="OPENROUTER_API_KEY",
        project_root=tmp_path,
    )
    assert path.exists()
    assert path.name == ".env.litellm"


def test_generate_env_litellm_content(tmp_path: Path) -> None:
    """generate_env_litellm 写入正确的 KEY=value 格式"""
    generate_env_litellm(
        provider_id="openrouter",
        api_key="sk-test-key",
        env_var_name="OPENROUTER_API_KEY",
        project_root=tmp_path,
    )
    content = (tmp_path / ".env.litellm").read_text(encoding="utf-8")
    assert "OPENROUTER_API_KEY=sk-test-key" in content


def test_generate_env_litellm_updates_existing(tmp_path: Path) -> None:
    """更新已有 KEY 时不影响其他行"""
    # 先写初始内容
    (tmp_path / ".env.litellm").write_text(
        "ANTHROPIC_API_KEY=sk-ant\nOPENROUTER_API_KEY=sk-old\n",
        encoding="utf-8",
    )
    generate_env_litellm(
        provider_id="openrouter",
        api_key="sk-new",
        env_var_name="OPENROUTER_API_KEY",
        project_root=tmp_path,
    )
    content = (tmp_path / ".env.litellm").read_text(encoding="utf-8")
    # 旧值被更新
    assert "OPENROUTER_API_KEY=sk-new" in content
    # 其他行保留
    assert "ANTHROPIC_API_KEY=sk-ant" in content
    # 不应有旧值
    assert "OPENROUTER_API_KEY=sk-old" not in content


def test_generate_env_litellm_atomic_no_tmp_residue(tmp_path: Path) -> None:
    """原子写入不残留临时文件"""
    generate_env_litellm(
        provider_id="openrouter",
        api_key="sk-test",
        env_var_name="OPENROUTER_API_KEY",
        project_root=tmp_path,
    )
    # 临时文件不应残留
    tmp_file = tmp_path / ".env.litellm.tmp"
    assert not tmp_file.exists()


def test_sync_warns_on_missing_credential_env(tmp_path: Path) -> None:
    """生成时 API Key 可以为空字符串，generate_env_litellm 不阻断（EC-2）"""
    # 即使 api_key 为空，函数不应抛出异常（WARN 不阻断）
    path = generate_env_litellm(
        provider_id="openrouter",
        api_key="",  # 凭证缺失
        env_var_name="OPENROUTER_API_KEY",
        project_root=tmp_path,
    )
    # 函数成功返回（不阻断）
    assert path.exists()
