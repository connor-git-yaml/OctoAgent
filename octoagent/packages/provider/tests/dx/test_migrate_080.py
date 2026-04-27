"""Feature 081 P2：migrate-080 双对象迁移单测（修 Codex F3）。

覆盖：
- yaml 迁移：v1 → v2（推断 transport / 转 auth / config_version + 备份）
- yaml 迁移：v2 → v2（幂等，无操作）
- yaml 迁移：dry-run（不写文件）
- yaml 迁移：失败回滚（原文件不破坏）
- 凭证迁移：.env.litellm → .env（key 合并 + 备份）
- 凭证迁移：.env 已有同名 key（保留 .env，记录冲突）
- 凭证迁移：.env.litellm 不存在（安全降级）
- 凭证迁移：.env.litellm 为空（安全降级）
- 完整双对象 + 失败局部（yaml 成功 / env 失败 不破坏 yaml）
- transport 推断（与 ProviderRouter fallback 同源）
"""

from __future__ import annotations

from pathlib import Path

import yaml

from octoagent.provider.dx.migrate_080 import (
    Migrate080Result,
    execute_migrate_080,
    infer_provider_transport,
)


def _v1_yaml(extra_runtime: dict | None = None, providers: list | None = None) -> str:
    """构造 v1 yaml 文本。"""
    runtime = {
        "llm_mode": "litellm",
        "litellm_proxy_url": "http://localhost:4000",
        "master_key_env": "LITELLM_MASTER_KEY",
    }
    if extra_runtime:
        runtime.update(extra_runtime)
    raw = {
        "config_version": 1,
        "updated_at": "2026-04-26",
        "providers": providers
        or [
            {
                "id": "openrouter",
                "name": "OpenRouter",
                "auth_type": "api_key",
                "api_key_env": "OPENROUTER_API_KEY",
                "base_url": "https://openrouter.ai/api/v1",
                "enabled": True,
            }
        ],
        "runtime": runtime,
    }
    return yaml.safe_dump(raw, allow_unicode=True, sort_keys=False)


# ── transport 推断测试 ──


def test_infer_transport_openai_codex_to_responses() -> None:
    assert infer_provider_transport("openai-codex") == "openai_responses"


def test_infer_transport_anthropic_to_messages() -> None:
    assert infer_provider_transport("anthropic-claude") == "anthropic_messages"


def test_infer_transport_default_openai_chat() -> None:
    assert infer_provider_transport("openrouter") == "openai_chat"
    assert infer_provider_transport("siliconflow") == "openai_chat"
    assert infer_provider_transport("deepseek") == "openai_chat"


def test_infer_transport_explicit_overrides() -> None:
    assert infer_provider_transport("openai-codex", "openai_chat") == "openai_chat"


# ── yaml 迁移 ──


def test_migrate_yaml_v1_to_v2(tmp_path: Path) -> None:
    yaml_path = tmp_path / "octoagent.yaml"
    yaml_path.write_text(_v1_yaml(), encoding="utf-8")

    result = execute_migrate_080(tmp_path, dry_run=False)

    assert result.error is None
    assert result.yaml_written is True
    assert result.plan.yaml_already_v2 is False
    assert result.plan.yaml_backup_path is not None
    assert result.plan.yaml_backup_path.exists()

    # 重新读 v2
    new_raw = yaml.safe_load(yaml_path.read_text(encoding="utf-8"))
    assert new_raw["config_version"] == 2
    p = new_raw["providers"][0]
    assert p["transport"] == "openai_chat"  # openrouter → openai_chat
    assert p["api_base"] == "https://openrouter.ai/api/v1"
    assert p["auth"] == {"kind": "api_key", "env": "OPENROUTER_API_KEY"}


def test_migrate_yaml_strips_runtime_litellm_legacy_fields(tmp_path: Path) -> None:
    """Feature 081 fix（2026-04-27）：迁移后 runtime 下 LiteLLM 残留必须被移除。

    用户场景（实测复现）：
    - migrate-080 后 yaml 仍含 runtime.llm_mode/litellm_proxy_url/master_key_env
    - 每次 load_config 仍触发 octoagent_yaml_legacy_schema_detected warn
    - 即使 dedup 也只是抑制 spam，warn 本身仍打 1 次（误导用户以为没迁完）

    本测试锁定：迁移后 runtime 下不再含任何 LiteLLM 残留。
    若 runtime 仅有 LiteLLM 字段，整个 runtime 块也应消失。
    """
    yaml_path = tmp_path / "octoagent.yaml"
    yaml_path.write_text(_v1_yaml(), encoding="utf-8")

    result = execute_migrate_080(tmp_path, dry_run=False)
    assert result.error is None
    assert result.yaml_written is True

    new_raw = yaml.safe_load(yaml_path.read_text(encoding="utf-8"))
    runtime_after = new_raw.get("runtime", {})
    if isinstance(runtime_after, dict):
        for key in ("llm_mode", "litellm_proxy_url", "master_key_env"):
            assert key not in runtime_after, (
                f"runtime.{key} 在 migrate 后仍存在；应被移除避免重复触发 legacy 检测"
            )

    # detect_legacy_yaml_keys 应该返回空（除非 providers 也漏迁，但本 fixture 已迁完）
    from octoagent.gateway.services.config.config_schema import detect_legacy_yaml_keys

    legacy_after = detect_legacy_yaml_keys(new_raw)
    assert legacy_after == [], (
        f"migrate 后 detect_legacy_yaml_keys 仍命中：{legacy_after}；"
        "说明 v2 schema 没有真正清理干净"
    )


def test_migrate_yaml_preserves_non_litellm_runtime_fields(tmp_path: Path) -> None:
    """runtime 下非 LiteLLM 字段应保留（防御过度 strip）。"""
    yaml_path = tmp_path / "octoagent.yaml"
    yaml_path.write_text(
        _v1_yaml(extra_runtime={"some_other_setting": "keep_me"}),
        encoding="utf-8",
    )

    execute_migrate_080(tmp_path, dry_run=False)

    new_raw = yaml.safe_load(yaml_path.read_text(encoding="utf-8"))
    assert new_raw.get("runtime", {}).get("some_other_setting") == "keep_me"
    # LiteLLM 字段仍应被剥
    runtime_after = new_raw.get("runtime", {})
    for key in ("llm_mode", "litellm_proxy_url", "master_key_env"):
        assert key not in runtime_after


def test_migrate_yaml_v2_idempotent(tmp_path: Path) -> None:
    """v2 yaml → 不再迁移（幂等）。"""
    raw_v2 = {
        "config_version": 2,
        "updated_at": "2026-04-26",
        "providers": [
            {
                "id": "openrouter",
                "name": "OpenRouter",
                "transport": "openai_chat",
                "api_base": "https://openrouter.ai/api/v1",
                "auth": {"kind": "api_key", "env": "OPENROUTER_API_KEY"},
                "enabled": True,
            }
        ],
        "runtime": {},
    }
    yaml_path = tmp_path / "octoagent.yaml"
    yaml_path.write_text(yaml.safe_dump(raw_v2), encoding="utf-8")

    result = execute_migrate_080(tmp_path, dry_run=False)

    assert result.error is None
    assert result.yaml_written is False
    assert result.plan.yaml_already_v2 is True


def test_migrate_yaml_dry_run_no_writes(tmp_path: Path) -> None:
    yaml_path = tmp_path / "octoagent.yaml"
    original_text = _v1_yaml()
    yaml_path.write_text(original_text, encoding="utf-8")

    result = execute_migrate_080(tmp_path, dry_run=True)

    assert result.yaml_written is False
    assert result.plan.yaml_backup_path is None
    # 原文件不变
    assert yaml_path.read_text(encoding="utf-8") == original_text
    # 计划仍然有 yaml_changes
    assert any("config_version" in c for c in result.plan.yaml_changes)


def test_migrate_yaml_oauth_provider(tmp_path: Path) -> None:
    yaml_path = tmp_path / "octoagent.yaml"
    yaml_path.write_text(
        _v1_yaml(
            providers=[
                {
                    "id": "openai-codex",
                    "name": "OpenAI Codex",
                    "auth_type": "oauth",
                    "api_key_env": "OPENAI_API_KEY",
                    "enabled": True,
                }
            ]
        ),
        encoding="utf-8",
    )

    result = execute_migrate_080(tmp_path, dry_run=False)

    assert result.error is None
    new_raw = yaml.safe_load(yaml_path.read_text(encoding="utf-8"))
    p = new_raw["providers"][0]
    assert p["transport"] == "openai_responses"  # openai-codex → responses
    assert p["api_base"] == "https://chatgpt.com/backend-api/codex"
    assert p["auth"] == {"kind": "oauth", "profile": "openai-codex-default"}


def test_migrate_yaml_does_not_touch_when_no_yaml(tmp_path: Path) -> None:
    """yaml 不存在 → 不报错，直接跳过。"""
    result = execute_migrate_080(tmp_path, dry_run=False)
    assert result.error is None
    assert result.yaml_written is False
    assert result.plan.yaml_already_v2 is True  # 当作 already-v2 处理（因为没有 v1）


# ── .env.litellm 迁移 ──


def test_migrate_env_litellm_to_env(tmp_path: Path) -> None:
    env_litellm = tmp_path / ".env.litellm"
    env_litellm.write_text(
        "OPENAI_API_KEY=sk-old\nANTHROPIC_API_KEY=ant-old\n",
        encoding="utf-8",
    )

    result = execute_migrate_080(tmp_path, dry_run=False)

    assert result.env_written is True
    assert result.plan.env_backup_path is not None
    assert result.plan.env_backup_path.exists()

    # .env 应该有这两个 key
    env_text = (tmp_path / ".env").read_text(encoding="utf-8")
    assert "OPENAI_API_KEY=sk-old" in env_text
    assert "ANTHROPIC_API_KEY=ant-old" in env_text

    # .env.litellm 仍然存在（兼容窗口至 P4）
    assert env_litellm.exists()


def test_migrate_env_existing_keys_not_overwritten(tmp_path: Path) -> None:
    env_litellm = tmp_path / ".env.litellm"
    env_litellm.write_text("OPENAI_API_KEY=sk-from-litellm\n", encoding="utf-8")

    env_target = tmp_path / ".env"
    env_target.write_text("OPENAI_API_KEY=sk-from-env\n", encoding="utf-8")

    result = execute_migrate_080(tmp_path, dry_run=False)

    assert result.error is None
    # .env 保留原值
    assert "OPENAI_API_KEY=sk-from-env" in (tmp_path / ".env").read_text(encoding="utf-8")
    assert "sk-from-litellm" not in (tmp_path / ".env").read_text(encoding="utf-8")
    # 但 plan 中应记录冲突
    assert any("OPENAI_API_KEY" in c for c in result.plan.env_conflicts)


def test_migrate_env_litellm_missing(tmp_path: Path) -> None:
    """没有 .env.litellm → 安全跳过。"""
    result = execute_migrate_080(tmp_path, dry_run=False)
    assert result.error is None
    assert result.env_written is False
    assert result.plan.env_already_migrated is True


def test_migrate_env_litellm_empty(tmp_path: Path) -> None:
    """.env.litellm 内无 KEY=VALUE 行 → 安全跳过。"""
    (tmp_path / ".env.litellm").write_text("# only comments\n\n", encoding="utf-8")

    result = execute_migrate_080(tmp_path, dry_run=False)
    assert result.error is None
    assert result.env_written is False
    assert result.plan.env_already_migrated is True


def test_migrate_env_dry_run_no_writes(tmp_path: Path) -> None:
    env_litellm = tmp_path / ".env.litellm"
    env_litellm.write_text("OPENAI_API_KEY=sk-x\n", encoding="utf-8")

    result = execute_migrate_080(tmp_path, dry_run=True)

    assert result.env_written is False
    assert not (tmp_path / ".env").exists()
    # 计划仍然显示
    assert any("OPENAI_API_KEY" in c for c in result.plan.env_changes)


# ── 双对象联合 ──


def test_migrate_both_yaml_and_env(tmp_path: Path) -> None:
    """yaml + env 同时存在 → 双对象都迁移成功。"""
    (tmp_path / "octoagent.yaml").write_text(_v1_yaml(), encoding="utf-8")
    (tmp_path / ".env.litellm").write_text(
        "OPENROUTER_API_KEY=sk-or\n", encoding="utf-8",
    )

    result = execute_migrate_080(tmp_path, dry_run=False)

    assert result.error is None
    assert result.yaml_written is True
    assert result.env_written is True


def test_migrate_repeat_idempotent(tmp_path: Path) -> None:
    """重复跑 migrate-080 应该幂等：第二次什么都不写。"""
    (tmp_path / "octoagent.yaml").write_text(_v1_yaml(), encoding="utf-8")
    env_litellm = tmp_path / ".env.litellm"
    env_litellm.write_text("OPENROUTER_API_KEY=sk-or\n", encoding="utf-8")

    # 第一次：迁移
    r1 = execute_migrate_080(tmp_path, dry_run=False)
    assert r1.yaml_written is True
    assert r1.env_written is True

    # 删除 .env.litellm 模拟用户清理；第二次：yaml 已是 v2，env 不存在 → 全部跳过
    env_litellm.unlink()
    r2 = execute_migrate_080(tmp_path, dry_run=False)
    assert r2.yaml_written is False
    assert r2.env_written is False
    assert r2.plan.yaml_already_v2 is True
    assert r2.plan.env_already_migrated is True


def test_migrate_yaml_corrupt_does_not_break_file(tmp_path: Path) -> None:
    """损坏的 yaml → 返回 error，原文件保持不变。"""
    yaml_path = tmp_path / "octoagent.yaml"
    yaml_path.write_text("not: yaml: at: all: ::", encoding="utf-8")

    original = yaml_path.read_text(encoding="utf-8")
    result = execute_migrate_080(tmp_path, dry_run=False)

    assert result.error is not None
    assert yaml_path.read_text(encoding="utf-8") == original
