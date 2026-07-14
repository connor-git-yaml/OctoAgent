"""F111 AC-11：behavior compact 全链 L3 scripted e2e（拍板④b）。

**链路**（零真 LLM / 零宿主 OAuth / CI-runnable）：

    脚本化 compact LLM（契约 A' 输出）→ 真 BehaviorCompactionService.run_manual
    → 真 BehaviorCompactDiscoveryService（占位符/护栏/幂等全真）→ 真
    behavior_compact_candidates 持久化 → **真 REST POST
    /api/behavior/compact/trigger + /candidates/{id}/accept|reject**（与 CLI/Web
    同一条路）→ 真 commit_behavior_file_write 覆写 → 真 F107 版本历史 → 事件链。

**脚本缝位置归档（spec §6 AC-11 注 / tests/AGENTS.md marker 表）**：F111 v0.1 无
决策环工具（`behavior.compact` LLM 工具 defer v0.2），compact 管道的 LLM 缝是
message-adapter 协议（``complete(messages=...)``）而非 SkillRunner 协议——脚本脑
实现前者，经 ``BehaviorCompactionService.llm_client`` **公开注入缝**（harness
bootstrap 后替换）进入。与 e2e_scripted 行核心语义一致：脚本化 LLM 输出驱动全链
确定性验证。

零真调用三重防御（F138 keystone 同款）：resolve_for_alias bomb + 空 tmp
CredentialStore + 末尾脚本内容贯穿断言。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

# pre-merge 窗口防御（F138 spec §3.5 同款）：pre-commit hook 可能以非本 worktree
# 的 src 收集本文件，彼时 F111 新模块不存在 → 优雅 SKIP。
pytest.importorskip("octoagent.gateway.services.behavior_compaction")

from httpx import ASGITransport, AsyncClient

pytestmark = [pytest.mark.e2e_scripted, pytest.mark.e2e_live]

#: 含语义重复规则 + PROTECTED 区段的 AGENTS.md 植入物
_PROTECTED_SECTION = (
    "<!-- 🔒 PROTECTED -->\n- 核心红线：绝不删除用户数据\n<!-- /🔒 PROTECTED -->"
)
_ORIGINAL = (
    "# AGENTS\n\n"
    f"{_PROTECTED_SECTION}\n\n"
    "- 回复保持简洁，不要冗长啰嗦\n"
    "- 回答用户时尽量简短，避免长篇大论的展开\n"
    "- 输出务必精炼，不要重复表达同一个意思\n"
    "- 内容要点到为止，不需要过度铺垫和客套\n"
    "- commit message 用中文书写\n"
    "- 提交说明必须使用中文\n"
    "- 所有 git 提交信息一律采用中文表述\n"
    "- 遇到不确定的事情要先询问用户再动手\n"
    "- 拿不准的操作先跟用户确认\n"
)
_COMPACTED_BODY = (
    "# AGENTS\n\n"
    "<<<PROTECTED_0>>>\n\n"
    "- 回复简洁精炼，不啰嗦\n"
    "- commit message 用中文\n"
    "- 不确定先问用户\n"
)


class _ScriptedCompactLLM:
    """契约 A' 输出的脚本 compact 脑（message-adapter 协议）。"""

    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    async def complete(self, messages: list[dict[str, str]], **kwargs: Any) -> Any:
        self.calls.append({"messages": messages, **kwargs})
        text = (
            "===COMPACTED===\n"
            f"{_COMPACTED_BODY}\n"
            "===RATIONALE===\n"
            "合并了简洁性 3 条与中文提交 3 条两组语义重复规则"
        )

        class _R:
            content = text

        return _R()


def _empty_credential_store(root: Path) -> Any:
    from octoagent.provider.auth.store import CredentialStore

    return CredentialStore(store_path=root / "creds" / "auth-profiles.json")


def _resolve_for_alias_bomb(*_args: Any, **_kwargs: Any) -> Any:
    raise AssertionError(
        "F111 AC-11: 真 provider 解析被触发——脚本化 compact 不允许任何真 LLM 调用"
    )


async def _build_app(root: Path):
    """真 harness bootstrap + 真 behavior compact REST 路由（与 CLI/Web 同路）。"""
    from fastapi import FastAPI
    from octoagent.gateway.harness.octo_harness import OctoHarness
    from octoagent.gateway.routes import behavior_compact as compact_routes

    (root / "behavior" / "system").mkdir(parents=True, exist_ok=True)
    (root / "data").mkdir(parents=True, exist_ok=True)
    (root / "mcp-servers").mkdir(parents=True, exist_ok=True)
    fixtures_root = (
        Path(__file__).resolve().parents[4] / "tests" / "fixtures" / "local-instance"
    )
    if (fixtures_root / "octoagent.yaml.template").exists():
        from apps.gateway.tests.e2e_live.helpers.factories import (
            copy_local_instance_template,
        )

        copy_local_instance_template(fixtures_root, root)

    harness = OctoHarness(
        project_root=root,
        credential_store=_empty_credential_store(root),
        mcp_servers_dir=root / "mcp-servers",
        data_dir=root / "data",
    )
    app = FastAPI()
    await harness.bootstrap(app)
    harness.commit_to_app(app)
    app.include_router(compact_routes.router)
    app.state.provider_router.resolve_for_alias = _resolve_for_alias_bomb
    return harness, app


async def _drive_compact(
    tmp_path: Path, *, decision: str
) -> dict[str, Any]:
    """驱动全链：植入 → 脚本缝替换 → REST trigger → REST 决策 → 现场返回。"""
    from octoagent.core.models.enums import EventType

    root = tmp_path / "octoagent_e2e_root"
    harness, app = await _build_app(root)
    try:
        # 装配断言：harness 真把编排服务放上 app.state（AC-11 前提）
        service = app.state.behavior_compaction_service
        assert service is not None, "harness 未装配 behavior_compaction_service"
        scripted = _ScriptedCompactLLM()
        service.llm_client = scripted  # ★ 公开注入缝（spec AC-11 注）

        # 植入含冗余规则的 AGENTS.md（bootstrap 后覆写，压过模板默认内容）
        agents_md = root / "behavior" / "system" / "AGENTS.md"
        agents_md.write_text(_ORIGINAL, encoding="utf-8")

        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            # 1. 手动触发（与 CLI 同一 REST 路）
            trigger = await client.post(
                "/api/behavior/compact/trigger", json={"file_id": "AGENTS.md"}
            )
            assert trigger.status_code == 200, trigger.text
            t_body = trigger.json()
            assert t_body["proposals_made"] == 1
            outcome = t_body["outcomes"][0]
            candidate_id = outcome["candidate_id"]

            # 2. 触发后、决策前：文件必须未变（C4——发现端绝不落盘）
            assert agents_md.read_text(encoding="utf-8") == _ORIGINAL

            # 3. 候选列表（人审 diff 载体）
            listing = await client.get("/api/behavior/compact/candidates")
            assert listing.status_code == 200
            items = listing.json()["candidates"]
            assert [c["candidate_id"] for c in items] == [candidate_id]
            assert "AGENTS.md（当前）" in items[0]["diff"]

            # 4. 用户决策（与 Web/CLI 同一 REST 路）
            decide = await client.post(
                f"/api/behavior/compact/candidates/{candidate_id}/{decision}"
            )
            assert decide.status_code == 200, decide.text

        # ★ 全部 DB 快照在 shutdown 前采集（store_group 连接随 shutdown 关闭）
        from octoagent.core.behavior_workspace import behavior_version_key_from_path

        sg = app.state.store_group
        events = list(
            await sg.event_store.get_events_for_task("_behavior_compact_root")
        )
        key = behavior_version_key_from_path(root, agents_md)
        version_metas = await sg.behavior_version_store.list_versions(key)
        version_contents = []
        for meta in version_metas:
            v = await sg.behavior_version_store.get_version_content(
                key, meta.version_no
            )
            version_contents.append(v.content if v is not None else None)
        candidate = await sg.behavior_compact_store.get_candidate(candidate_id)
        return {
            "root": root,
            "agents_md": agents_md,
            "scripted": scripted,
            "candidate_id": candidate_id,
            "candidate_status": str(candidate.status.value) if candidate else "",
            "outcome": outcome,
            "events": events,
            "event_types": {
                str(e.type.value if hasattr(e.type, "value") else e.type)
                for e in events
            },
            # DESC：[0]=最新版，[-1]=baseline
            "version_contents": version_contents,
            "EventType": EventType,
        }
    finally:
        await harness.shutdown(app)


async def test_scripted_compact_accept_full_chain(tmp_path: Path) -> None:
    """accept 半边：脚本提议 → 审后落盘 + PROTECTED 字节级保留 + F107 版本 + 事件链。"""
    ctx = await _drive_compact(tmp_path, decision="accept")
    EventType = ctx["EventType"]

    # 1. 落盘：精简后内容 + PROTECTED 区段字节级原样（H2 全链形态）
    final = ctx["agents_md"].read_text(encoding="utf-8")
    assert _PROTECTED_SECTION in final
    assert "<<<PROTECTED_" not in final
    assert len(final) < len(_ORIGINAL)
    assert "回复简洁精炼" in final  # 脚本内容贯穿到盘（零真调用防御 #3）

    # 2. 脚本脑真被消费（发现端真跑了 LLM 步）
    assert len(ctx["scripted"].calls) == 1
    prompt = ctx["scripted"].calls[0]["messages"][0]["content"]
    assert "绝不删除用户数据" not in prompt  # 占位符方案：LLM 看不到受保护内容

    # 3. F107 版本历史（可回滚兜底）：baseline(原文) + 新版(精简后)
    assert len(ctx["version_contents"]) == 2
    assert ctx["version_contents"][0] == final  # 最新版 = 盘上精简后内容
    assert ctx["version_contents"][-1] == _ORIGINAL  # baseline = 原文

    # 4. 事件链：TRIGGERED(manual) → PROPOSED → APPLIED 全挂 root task
    assert EventType.BEHAVIOR_COMPACT_TRIGGERED.value in ctx["event_types"]
    assert EventType.BEHAVIOR_COMPACT_PROPOSED.value in ctx["event_types"]
    assert EventType.BEHAVIOR_COMPACT_APPLIED.value in ctx["event_types"]
    triggered = [
        e for e in ctx["events"] if e.type == EventType.BEHAVIOR_COMPACT_TRIGGERED
    ]
    assert triggered[0].payload["trigger"] == "manual"
    proposed = [
        e for e in ctx["events"] if e.type == EventType.BEHAVIOR_COMPACT_PROPOSED
    ]
    # PII/体积纪律：事件 payload 不含原文/精简后全文
    assert "回复简洁精炼" not in str(proposed[0].payload)

    # 5. 候选终态 applied
    assert ctx["candidate_status"] == "applied"


async def test_scripted_compact_reject_no_write(tmp_path: Path) -> None:
    """reject 半边：拒绝后文件零触碰 + REJECTED 事件 + 无版本记录。"""
    ctx = await _drive_compact(tmp_path, decision="reject")
    EventType = ctx["EventType"]

    # 1. 文件与植入原文完全一致（零触碰）
    assert ctx["agents_md"].read_text(encoding="utf-8") == _ORIGINAL

    # 2. REJECTED 事件 + 无 APPLIED
    assert EventType.BEHAVIOR_COMPACT_REJECTED.value in ctx["event_types"]
    assert EventType.BEHAVIOR_COMPACT_APPLIED.value not in ctx["event_types"]

    # 3. 无版本记录（没落盘就没版本）
    assert ctx["version_contents"] == []

    # 4. 候选终态 rejected
    assert ctx["candidate_status"] == "rejected"
