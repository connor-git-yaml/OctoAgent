"""Feature 080/081 Migration —— octoagent.yaml v1 → v2 + .env.litellm → .env。

修订自原 plan FR-4，按 Codex F3 finding 扩展为**双对象迁移**：
- yaml 迁移：v1 schema（auth_type / api_key_env / base_url + runtime.llm_mode 等）
  → v2 schema（auth.kind / auth.env / api_base + transport first-class +
  config_version: 2）
- 凭证迁移：``~/.octoagent/.env.litellm`` 内容 merge 到 ``~/.octoagent/.env``，
  原文件备份为 ``.env.litellm.bak.080-{timestamp}``

设计原则：
- 失败不破坏原文件——所有写入前先备份，写入用临时文件 + os.replace 原子替换
- 幂等——重复执行检测 ``config_version: 2`` 即 skip
- ``--dry-run`` 输出 diff，不写文件
- 凭证保留兼容窗口——``.env.litellm`` 备份后**保留**原文件至 P4 完成（不主动删除）
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

import structlog
import yaml

log = structlog.get_logger()

__all__ = [
    "Migrate080Plan",
    "Migrate080Result",
    "execute_migrate_080",
    "infer_provider_transport",
]


# ── Transport / api_base 推断（与 ProviderRouter._provider_transport / _api_base 同源）──

_PROVIDER_TRANSPORT_DEFAULTS: dict[str, str] = {
    "openai-codex": "openai_responses",
    "anthropic-claude": "anthropic_messages",
    # 其他 id 默认 openai_chat（覆盖 SiliconFlow / DeepSeek / OpenRouter / OpenAI 等）
}

_PROVIDER_API_BASE_DEFAULTS: dict[str, str] = {
    "openai-codex": "https://chatgpt.com/backend-api/codex",
    "anthropic-claude": "https://api.anthropic.com",
    "openai": "https://api.openai.com",
    "siliconflow": "https://api.siliconflow.cn/v1",
}


def infer_provider_transport(provider_id: str, explicit: str | None = None) -> str:
    """推断 provider 的 transport（与 ProviderRouter fallback 同源）。"""
    if explicit:
        return explicit
    return _PROVIDER_TRANSPORT_DEFAULTS.get(provider_id, "openai_chat")


def _infer_provider_api_base(provider_id: str, explicit: str | None = None) -> str:
    """推断 api_base（迁移时兜底用）。"""
    if explicit:
        return explicit
    return _PROVIDER_API_BASE_DEFAULTS.get(provider_id, "")


# ── Plan / Result 数据结构 ──


@dataclass
class Migrate080Plan:
    """迁移计划（dry-run 时也产出）。"""

    yaml_already_v2: bool = False
    """yaml 已是 v2 → 不需要迁移。"""

    yaml_path: Path | None = None
    yaml_backup_path: Path | None = None
    yaml_changes: list[str] = field(default_factory=list)
    """人类可读的变更列表（用于 dry-run 展示）。"""

    env_litellm_path: Path | None = None
    env_target_path: Path | None = None
    env_backup_path: Path | None = None
    env_changes: list[str] = field(default_factory=list)
    env_already_migrated: bool = False
    env_conflicts: list[str] = field(default_factory=list)


@dataclass
class Migrate080Result:
    """迁移执行结果。"""

    plan: Migrate080Plan
    yaml_written: bool = False
    env_written: bool = False
    error: str | None = None


# ── YAML migration ──


def _backup_path(original: Path, kind: str, ts: str) -> Path:
    """生成 ``original.bak.080-{kind}-{timestamp}`` 备份路径。"""
    suffix = f".bak.080-{kind}-{ts}" if kind else f".bak.080-{ts}"
    return original.with_suffix(original.suffix + suffix)


def _migrate_provider_block(p: dict[str, Any]) -> tuple[dict[str, Any], list[str]]:
    """单个 provider dict 的 v1 → v2 迁移。

    Returns:
        (new_provider_dict, change_descriptions)
    """
    changes: list[str] = []
    new_p: dict[str, Any] = {}

    pid = p.get("id", "")
    new_p["id"] = pid
    new_p["name"] = p.get("name", pid)
    if "enabled" in p:
        new_p["enabled"] = p["enabled"]

    # transport：显式优先；没有就推断
    explicit_transport = p.get("transport")
    transport = infer_provider_transport(pid, explicit_transport)
    new_p["transport"] = transport
    if not explicit_transport:
        changes.append(f"providers[{pid}].transport = '{transport}'（推断）")
    else:
        changes.append(f"providers[{pid}].transport = '{transport}'（保留显式值）")

    # api_base：显式优先 → base_url → 默认
    explicit_api_base = p.get("api_base") or p.get("base_url") or ""
    api_base = _infer_provider_api_base(pid, explicit_api_base)
    if api_base:
        new_p["api_base"] = api_base
        if "base_url" in p and "api_base" not in p:
            changes.append(f"providers[{pid}].base_url='{p['base_url']}' → api_base")
    elif "api_base" not in p and "base_url" not in p:
        # 没有显式且没有兜底默认 → 仍然写空字符串供用户后续手动填
        new_p["api_base"] = ""

    # auth：显式优先；没有则从 auth_type + api_key_env 迁移
    explicit_auth = p.get("auth")
    if isinstance(explicit_auth, dict) and explicit_auth.get("kind"):
        new_p["auth"] = explicit_auth
        changes.append(f"providers[{pid}].auth = {explicit_auth!r}（保留）")
    else:
        auth_type = p.get("auth_type", "")
        if auth_type == "api_key":
            api_key_env = p.get("api_key_env", "")
            if api_key_env:
                new_p["auth"] = {"kind": "api_key", "env": api_key_env}
                changes.append(
                    f"providers[{pid}].auth_type='api_key' + api_key_env='{api_key_env}'"
                    f" → auth = {{kind: api_key, env: {api_key_env}}}"
                )
            else:
                changes.append(
                    f"⚠️ providers[{pid}].auth_type='api_key' 但 api_key_env 缺失，跳过 auth 迁移"
                )
        elif auth_type == "oauth":
            profile_name = f"{pid}-default"
            new_p["auth"] = {"kind": "oauth", "profile": profile_name}
            changes.append(
                f"providers[{pid}].auth_type='oauth' → auth = {{kind: oauth, profile: {profile_name}}}"
            )
        else:
            changes.append(
                f"⚠️ providers[{pid}].auth_type='{auth_type}' 不识别，未迁移 auth 字段"
            )

    # extra_headers / extra_body 透传
    if "extra_headers" in p:
        new_p["extra_headers"] = p["extra_headers"]
    if "extra_body" in p:
        new_p["extra_body"] = p["extra_body"]

    return new_p, changes


def plan_yaml_migration(raw: dict[str, Any], yaml_path: Path) -> Migrate080Plan:
    """生成 yaml 迁移计划（不写文件）。"""
    plan = Migrate080Plan(yaml_path=yaml_path)

    config_version = raw.get("config_version", 1)
    try:
        if int(config_version) >= 2:
            plan.yaml_already_v2 = True
            plan.yaml_changes.append("config_version 已是 v2，无需迁移")
            return plan
    except (TypeError, ValueError):
        pass

    plan.yaml_changes.append(f"config_version: {config_version} → 2")

    providers = raw.get("providers")
    if isinstance(providers, list):
        new_providers: list[dict[str, Any]] = []
        for p in providers:
            if not isinstance(p, dict):
                new_providers.append(p)
                continue
            new_p, changes = _migrate_provider_block(p)
            plan.yaml_changes.extend(changes)
            new_providers.append(new_p)

    runtime = raw.get("runtime")
    if isinstance(runtime, dict):
        for legacy_key in ("llm_mode", "litellm_proxy_url", "master_key_env"):
            if legacy_key in runtime:
                plan.yaml_changes.append(
                    f"runtime.{legacy_key} = {runtime[legacy_key]!r}（保留为 deprecated，运行时已忽略）"
                )

    return plan


def _build_v2_yaml(raw: dict[str, Any]) -> dict[str, Any]:
    """基于 raw v1 yaml 构建 v2 dict（不写文件）。"""
    new_raw: dict[str, Any] = dict(raw)
    new_raw["config_version"] = 2

    providers = raw.get("providers")
    if isinstance(providers, list):
        new_providers: list[dict[str, Any]] = []
        for p in providers:
            if not isinstance(p, dict):
                new_providers.append(p)
                continue
            new_p, _ = _migrate_provider_block(p)
            new_providers.append(new_p)
        new_raw["providers"] = new_providers

    return new_raw


# ── .env.litellm migration ──


_ENV_LINE_RE = re.compile(r"^\s*([A-Z][A-Z0-9_]*)\s*=\s*(.*?)\s*$")


def _parse_env_file(path: Path) -> tuple[dict[str, str], list[str]]:
    """读取 .env 风格文件（支持 KEY=VALUE 行 + # 注释）。

    Returns:
        (kv_dict, raw_lines_for_preserving_comments)
    """
    kv: dict[str, str] = {}
    raw_lines: list[str] = []
    if not path.exists():
        return kv, raw_lines
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return kv, raw_lines
    for line in text.splitlines():
        raw_lines.append(line)
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        m = _ENV_LINE_RE.match(line)
        if m:
            kv[m.group(1)] = m.group(2)
    return kv, raw_lines


def plan_env_migration(env_litellm_path: Path, env_target_path: Path) -> Migrate080Plan:
    """生成 .env.litellm → .env 迁移计划。

    返回 partial Plan（仅 env_* 字段）；调用方应合并到主 Plan。
    """
    plan = Migrate080Plan(
        env_litellm_path=env_litellm_path,
        env_target_path=env_target_path,
    )

    if not env_litellm_path.exists():
        plan.env_already_migrated = True
        plan.env_changes.append(f"{env_litellm_path.name} 不存在，无需迁移")
        return plan

    src_kv, _ = _parse_env_file(env_litellm_path)
    if not src_kv:
        plan.env_already_migrated = True
        plan.env_changes.append(f"{env_litellm_path.name} 内无可迁移的 KEY=VALUE 行")
        return plan

    dst_kv, _ = _parse_env_file(env_target_path)

    for key, value in src_kv.items():
        if key in dst_kv:
            if dst_kv[key] == value:
                plan.env_changes.append(f"{key}：已存在于 {env_target_path.name}，值相同")
            else:
                plan.env_conflicts.append(
                    f"{key}：{env_target_path.name} 已有不同值，**保留** {env_target_path.name} 的值"
                )
        else:
            plan.env_changes.append(f"{key} → 将合并到 {env_target_path.name}")

    return plan


def _merge_env_files(
    src_kv: dict[str, str], dst_path: Path
) -> str:
    """把 src_kv 合并到 dst_path 的内容中（已存在的键不覆盖）。"""
    dst_kv, dst_lines = _parse_env_file(dst_path)

    new_lines = list(dst_lines)
    if new_lines and new_lines[-1].strip():
        new_lines.append("")

    new_lines.append("# Feature 081 P2 migrate-080：从 .env.litellm 自动合并")
    for key, value in src_kv.items():
        if key in dst_kv:
            continue
        new_lines.append(f"{key}={value}")

    return "\n".join(new_lines) + "\n"


# ── 主入口 ──


def execute_migrate_080(
    project_root: Path,
    *,
    dry_run: bool = False,
) -> Migrate080Result:
    """执行 yaml + .env.litellm 双对象迁移。

    Args:
        project_root: 项目根目录（含 octoagent.yaml）
        dry_run: True 时不写文件，仅产出 plan

    Returns:
        Migrate080Result（含 plan + 是否写入 + 错误信息）
    """
    yaml_path = project_root / "octoagent.yaml"
    env_litellm_path = project_root / ".env.litellm"
    env_target_path = project_root / ".env"

    main_plan: Migrate080Plan

    # ── Phase 1：解析 yaml + 生成迁移计划 ──
    if not yaml_path.exists():
        main_plan = Migrate080Plan(yaml_path=yaml_path)
        main_plan.yaml_already_v2 = True
        main_plan.yaml_changes.append(f"{yaml_path} 不存在，跳过 yaml 迁移")
    else:
        try:
            raw = yaml.safe_load(yaml_path.read_text(encoding="utf-8"))
        except (yaml.YAMLError, OSError) as exc:
            return Migrate080Result(
                plan=Migrate080Plan(yaml_path=yaml_path),
                error=f"读取 octoagent.yaml 失败：{exc}",
            )
        if not isinstance(raw, dict):
            return Migrate080Result(
                plan=Migrate080Plan(yaml_path=yaml_path),
                error=f"octoagent.yaml 顶层不是 dict：{type(raw).__name__}",
            )
        main_plan = plan_yaml_migration(raw, yaml_path)

    # ── Phase 2：生成 env 迁移计划 ──
    env_plan = plan_env_migration(env_litellm_path, env_target_path)
    main_plan.env_litellm_path = env_plan.env_litellm_path
    main_plan.env_target_path = env_plan.env_target_path
    main_plan.env_changes = env_plan.env_changes
    main_plan.env_already_migrated = env_plan.env_already_migrated
    main_plan.env_conflicts = env_plan.env_conflicts

    if dry_run:
        return Migrate080Result(plan=main_plan)

    # ── Phase 3：执行 yaml 写入 ──
    result = Migrate080Result(plan=main_plan)
    ts = datetime.now().strftime("%Y%m%d-%H%M%S")

    if yaml_path.exists() and not main_plan.yaml_already_v2:
        try:
            raw = yaml.safe_load(yaml_path.read_text(encoding="utf-8"))
            new_raw = _build_v2_yaml(raw)

            backup_path = _backup_path(yaml_path, "yaml", ts)
            backup_path.write_text(yaml_path.read_text(encoding="utf-8"), encoding="utf-8")
            main_plan.yaml_backup_path = backup_path

            tmp_path = yaml_path.with_suffix(yaml_path.suffix + ".tmp.080")
            tmp_path.write_text(
                yaml.safe_dump(new_raw, allow_unicode=True, sort_keys=False),
                encoding="utf-8",
            )
            os.replace(tmp_path, yaml_path)
            result.yaml_written = True
        except Exception as exc:
            result.error = f"yaml 迁移失败：{exc}"
            return result

    # ── Phase 4：执行 env 写入 ──
    if env_litellm_path.exists() and not main_plan.env_already_migrated:
        try:
            src_kv, _ = _parse_env_file(env_litellm_path)
            if src_kv:
                env_backup = _backup_path(env_litellm_path, "env", ts)
                env_backup.write_text(env_litellm_path.read_text(encoding="utf-8"), encoding="utf-8")
                main_plan.env_backup_path = env_backup

                merged_text = _merge_env_files(src_kv, env_target_path)
                env_tmp = env_target_path.with_suffix(env_target_path.suffix + ".tmp.080")
                env_tmp.write_text(merged_text, encoding="utf-8")
                os.replace(env_tmp, env_target_path)
                result.env_written = True
                # 不删除 .env.litellm 原文件——保留兼容读取窗口至 P4
        except Exception as exc:
            result.error = f".env.litellm 迁移失败（yaml 已成功）：{exc}"
            return result

    return result
