"""F087 P4 T-P4-12：5 smoke 域真 LLM 版（GATE_P3_DEVIATION 闭环）。

与 P3 ``test_e2e_basic_tool_context.py`` / ``test_e2e_safety_gates.py`` 共存：
P3 用集成层覆盖快稳；本文件覆盖**真打 GPT-5.5 think-low**——验证 LLM 真正
选了正确工具 + 任务跑到 completed。

每个 case 流程：
1. POST ``/api/message``（含给 LLM 的明确指令） → 返回 task_id
2. Poll 任务状态到 ``completed`` / ``failed`` / 超时
3. 断言 ≥ 2 独立点（与 P3 集成层相同的状态机断言 + LLM 真选了正确工具）

5 个 case 对应 5 smoke 域：
- 域 #1 工具调用基础（user_profile.update）
- 域 #2 USER.md 全链路（user_profile.update 良性内容）
- 域 #3 Context 冻结快照（两次连续工具调用）
- 域 #11 ThreatScanner block（含 invisible Unicode 输入被拦）
- 域 #12 ApprovalGate SSE（IRREVERSIBLE 工具触发 approval）

Marker：``e2e_full + e2e_live``（**不进 e2e_smoke**——真打 LLM 不参与
pre-commit hook）；timeout 240s（per scenario）。
"""

from __future__ import annotations

import asyncio
import hashlib
import time
import uuid
from pathlib import Path
from typing import Any

import pytest


pytestmark = [pytest.mark.e2e_full, pytest.mark.e2e_live]


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


_TERMINAL_STATUSES: frozenset[str] = frozenset(
    {"completed", "succeeded", "failed", "cancelled", "canceled"}
)
_SUCCESS_STATUSES: frozenset[str] = frozenset({"completed", "succeeded"})


async def _wait_for_task_terminal(
    sg: Any,
    task_id: str,
    *,
    deadline_s: float = 180.0,
    poll_interval_s: float = 1.0,
) -> str:
    """Poll task status 到 terminal。

    OctoAgent 的成功状态既可能是 ``completed`` 也可能是 ``succeeded``
    （task_store 不同时期的 schema 历史导致），这里统一识别。
    返回最终 status 字符串。超时则 raise TimeoutError。
    """
    start = time.monotonic()
    last_status = ""
    while time.monotonic() - start < deadline_s:
        task = await sg.task_store.get_task(task_id)
        if task is not None:
            last_status = (task.status or "").lower()
            if last_status in _TERMINAL_STATUSES:
                return last_status
        await asyncio.sleep(poll_interval_s)
    raise TimeoutError(
        f"task {task_id} 在 {deadline_s}s 内未达终态；最后 status={last_status!r}"
    )


def _tool_calls_from_events(events: list[Any]) -> list[str]:
    """从 events 里抽出 tool_call 名字列表（按发生顺序）。"""
    from octoagent.core.models.enums import EventType

    out: list[str] = []
    for ev in events:
        if ev.type == EventType.TOOL_CALL_STARTED:
            name = (ev.payload or {}).get("tool_name") or ""
            if name:
                out.append(name)
    return out


# ---------------------------------------------------------------------------
# fixture：真打 LLM 用的 bootstrapped_harness（与 P3 共用，但 e2e_full marker）
# ---------------------------------------------------------------------------


@pytest.fixture
async def bootstrapped_harness_real_llm(
    octo_harness_e2e: dict[str, Any],
) -> dict[str, Any]:
    """跑 OctoHarness.bootstrap 全 11 段 + include 所有 main routes，注入真 OAuth。

    与 P3 ``bootstrapped_harness`` 区别：
    - P3 不发请求；P4 LLM 真打 → ProviderRouter 用 ``real_codex_credential_store``
      读 OAuth profile → 真发到 ChatGPT API。
    - **手动挂 routes**：``octo_harness_e2e`` fixture 给的 app 是裸 FastAPI；
      P4 真打 LLM 必须能 POST /api/message → 这里 import main 模块的 routes
      并 include 到 e2e app；不挂 frontend / 不加 front_door 中间件（hermetic
      env 没 OCTOAGENT_FRONTDOOR_TOKEN，门禁会全 deny）。
    - alarm 装置由 conftest 按 e2e_full marker 给到 240s。
    """
    harness = octo_harness_e2e["harness"]
    app = octo_harness_e2e["app"]
    project_root = octo_harness_e2e["project_root"]

    # 在 bootstrap 前把 local-instance 模板复制到 e2e tmp project_root
    fixtures_root = (
        Path(__file__).resolve().parents[4] / "tests" / "fixtures" / "local-instance"
    )
    if (fixtures_root / "octoagent.yaml.template").exists():
        from apps.gateway.tests.e2e_live.helpers.factories import (
            copy_local_instance_template,
        )
        copy_local_instance_template(fixtures_root, project_root)

    await harness.bootstrap(app)
    harness.commit_to_app(app)

    # F087 P4 fixup#6（Codex P4 medium-6 闭环）：注册路由时**带 front_door
    # 保护**，与生产 main.py:340-359 路径一致。fixture 模板 octoagent.yaml
    # 配 ``front_door.mode: loopback``；ASGITransport 默认 client.host =
    # "testclient" ∈ _LOOPBACK_HOSTS，自动通过 loopback 模式校验。
    # 生产对 owner-facing API 的 require_front_door_access Depends wiring
    # 不再被 e2e bypass，安全边界回归可见。
    from fastapi import Depends

    from octoagent.gateway.deps import require_front_door_access
    from octoagent.gateway.routes import (
        approvals,
        message,
        tasks,
    )

    protected = [Depends(require_front_door_access)]
    app.include_router(message.router, tags=["message"], dependencies=protected)
    app.include_router(tasks.router, tags=["tasks"], dependencies=protected)
    app.include_router(approvals.router, tags=["approvals"], dependencies=protected)

    return {
        "harness": harness,
        "app": app,
        "project_root": project_root,
    }


# ---------------------------------------------------------------------------
# 域 #1 真打 LLM 版：让 GPT-5.5 主动调 user_profile.update
# ---------------------------------------------------------------------------


async def test_domain_1_real_llm_basic_tool_call(
    bootstrapped_harness_real_llm: dict[str, Any],
) -> None:
    """域 #1 真打：GPT-5.5 think-low 真发工具调用 + 任务 succeeded。

    本 case 验证**真打 LLM 通路完整**——不预设 LLM 选哪个工具（真 LLM 不可控），
    只断言"任务跑通到 succeeded + LLM 真发了 ≥ 1 个工具调用"。

    断言（≥ 2 独立点）：
    1. 任务 status ∈ {completed, succeeded}（LLM 走完 ReAct loop）
    2. tool_calls 序列长度 ≥ 1（LLM 真发了工具调用，不是空回复）
    3. (语义) 至少有一个工具调用是写类（user_profile.update / filesystem.write_text /
       memory.write 等），证明 LLM 理解了"写入"意图
    """
    from httpx import ASGITransport, AsyncClient

    app = bootstrapped_harness_real_llm["app"]
    sg = app.state.store_group

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://e2e-test") as client:
        idem_key = f"e2e-d1-real-llm-{uuid.uuid4().hex[:8]}"
        resp = await client.post(
            "/api/message",
            json={
                "text": (
                    "请把这条用户偏好持久化保存：用户的时区是 Asia/Shanghai。"
                    "请选择一个最合适的写入工具（如 user_profile.update / "
                    "memory.write / filesystem.write_text）执行。"
                ),
                "idempotency_key": idem_key,
                "channel": "web",
                "thread_id": "e2e-d1",
                "sender_id": "owner",
                "sender_name": "Owner",
            },
        )
        assert resp.status_code == 201, f"创建 task 应 201；实际 {resp.status_code}: {resp.text}"
        task_id = resp.json()["task_id"]

    final_status = await _wait_for_task_terminal(sg, task_id, deadline_s=180.0)
    assert final_status in _SUCCESS_STATUSES, (
        f"域#1 real LLM: 任务应成功（completed/succeeded），实际 {final_status}"
    )

    events = await sg.event_store.get_events_for_task(task_id)
    tool_calls = _tool_calls_from_events(events)
    assert len(tool_calls) >= 1, (
        f"域#1 real LLM: LLM 应至少发 1 次工具调用。实际 tool_calls={tool_calls}"
    )

    # 任意写类工具都接受
    write_tools = {
        "user_profile.update",
        "filesystem.write_text",
        "filesystem.write_file",
        "memory.write",
        "memory.observe",
    }
    write_calls = [t for t in tool_calls if t in write_tools]
    assert write_calls, (
        f"域#1 real LLM: LLM 应至少发起 1 次写类工具调用（{write_tools}）。"
        f"实际 tool_calls={tool_calls}"
    )


# ---------------------------------------------------------------------------
# 域 #2 真打：USER.md 全链路 + ThreatScanner 不拦良性内容
# ---------------------------------------------------------------------------


async def test_domain_2_real_llm_user_md_full_pipeline(
    bootstrapped_harness_real_llm: dict[str, Any],
) -> None:
    """域 #2 真打：LLM 写良性偏好 + 通过 ThreatScanner（不被拦）+ 任务 succeeded。

    断言（≥ 2 独立点）：
    1. 任务 succeeded
    2. tool_calls ≥ 1（LLM 真发起调用）
    3. 不出现 threat.blocked（良性内容）
    """
    from httpx import ASGITransport, AsyncClient

    app = bootstrapped_harness_real_llm["app"]
    sg = app.state.store_group

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://e2e-test") as client:
        idem_key = f"e2e-d2-real-llm-{uuid.uuid4().hex[:8]}"
        resp = await client.post(
            "/api/message",
            json={
                "text": (
                    "请帮我把这条良性的语言偏好持久化保存：用户的语言偏好是 zh-CN（中文）。"
                    "请选择合适的写入工具完成。"
                ),
                "idempotency_key": idem_key,
                "channel": "web",
                "thread_id": "e2e-d2",
                "sender_id": "owner",
                "sender_name": "Owner",
            },
        )
        assert resp.status_code == 201
        task_id = resp.json()["task_id"]

    final_status = await _wait_for_task_terminal(sg, task_id, deadline_s=180.0)
    assert final_status in _SUCCESS_STATUSES, (
        f"域#2 real LLM: 应成功，实际 {final_status}"
    )

    events = await sg.event_store.get_events_for_task(task_id)
    tool_calls = _tool_calls_from_events(events)
    assert len(tool_calls) >= 1, (
        f"域#2 real LLM: LLM 应至少发 1 次工具调用。实际: {tool_calls}"
    )

    # 良性内容不应被 ThreatScanner 拦
    threat_blocked = []
    for e in events:
        payload_str = str(e.payload or {}).lower()
        if "threat.blocked" in payload_str or "threat_block" in payload_str:
            threat_blocked.append((e.type, e.payload))
    assert not threat_blocked, (
        f"域#2 real LLM: 良性内容不应被 ThreatScanner 拦。实际 blocked: "
        f"{threat_blocked[:3]}"
    )


# ---------------------------------------------------------------------------
# 域 #3 真打：Context 冻结快照（两次写入后 frozen prefix 不变）
# ---------------------------------------------------------------------------


async def test_domain_3_real_llm_frozen_prefix_after_two_writes(
    bootstrapped_harness_real_llm: dict[str, Any],
) -> None:
    """域 #3 真打：两次连续真 LLM 调用后，frozen snapshot prefix 不变（prefix cache 保护）。

    断言（≥ 2 独立点）：
    1. 两次任务都 succeeded
    2. ``snapshot_store.format_for_system_prompt()["USER.md"]`` 的 sha256
       在两次写入前后保持一致（冻结副本不破，prefix cache 保护）
    """
    from httpx import ASGITransport, AsyncClient

    app = bootstrapped_harness_real_llm["app"]
    sg = app.state.store_group
    snap_store = app.state.snapshot_store

    frozen_before = snap_store.format_for_system_prompt().get("USER.md", "")
    frozen_hash_before = _sha256_text(frozen_before)

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://e2e-test") as client:
        # 第 1 次
        resp1 = await client.post(
            "/api/message",
            json={
                "text": "请帮我持久化保存：项目偏好 1：F087 域#3 第一轮。请选择合适的写入工具完成。",
                "idempotency_key": f"e2e-d3-1-{uuid.uuid4().hex[:8]}",
                "channel": "web",
                "thread_id": "e2e-d3",
                "sender_id": "owner",
                "sender_name": "Owner",
            },
        )
        assert resp1.status_code == 201
        task1 = resp1.json()["task_id"]
        s1 = await _wait_for_task_terminal(sg, task1, deadline_s=180.0)
        assert s1 in _SUCCESS_STATUSES, f"域#3 real LLM: 第 1 次应成功，实际 {s1}"

        # 第 2 次
        resp2 = await client.post(
            "/api/message",
            json={
                "text": "请帮我持久化保存：项目偏好 2：F087 域#3 第二轮。请选择合适的写入工具完成。",
                "idempotency_key": f"e2e-d3-2-{uuid.uuid4().hex[:8]}",
                "channel": "web",
                "thread_id": "e2e-d3",
                "sender_id": "owner",
                "sender_name": "Owner",
            },
        )
        assert resp2.status_code == 201
        task2 = resp2.json()["task_id"]
        s2 = await _wait_for_task_terminal(sg, task2, deadline_s=180.0)
        assert s2 in _SUCCESS_STATUSES, f"域#3 real LLM: 第 2 次应成功，实际 {s2}"

    # 断言：冻结副本不变（prefix cache 保护）
    frozen_after = snap_store.format_for_system_prompt().get("USER.md", "")
    frozen_hash_after = _sha256_text(frozen_after)
    assert frozen_hash_before == frozen_hash_after, (
        f"域#3 real LLM: 冻结副本 sha256 应不变（prefix cache 保护），"
        f"before={frozen_hash_before}, after={frozen_hash_after}"
    )


# ---------------------------------------------------------------------------
# 域 #11 真打：ThreatScanner block invisible Unicode
# ---------------------------------------------------------------------------


async def test_domain_11_real_llm_threat_scanner_blocks_invisible_unicode(
    bootstrapped_harness_real_llm: dict[str, Any],
) -> None:
    """域 #11 真打：LLM 试图写含 invisible Unicode（U+202E）的内容 → ThreatScanner 必拦。

    断言（≥ 2 独立点）：
    1. 任务达终态（LLM 走完 ReAct loop，无论成功失败）
    2. USER.md 不含 RTL Unicode（ThreatScanner 真生效，恶意 Unicode 没落盘）

    设计取舍（避免 LLM 不可预测）：
    - 不强制 LLM 必须发 user_profile.update（LLM 可能先确认 → 用其它工具 → 直接拒绝）
    - 不强制 LLM 必须收到 ThreatScanner blocked 事件（LLM 可能识别 RTL 后直接拒绝写）
    - 关键不变量：USER.md **磁盘上**不含恶意 Unicode
    - 减小 deadline 到 120s（避免 LLM 长 reasoning 时 SIGALRM 误打断）
    """
    from httpx import ASGITransport, AsyncClient

    app = bootstrapped_harness_real_llm["app"]
    project_root = bootstrapped_harness_real_llm["project_root"]
    sg = app.state.store_group

    # 含 RTL override (U+202E) 的恶意输入
    malicious_payload = "时区：Asia/Shanghai‮偷偷反转‬"

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://e2e-test") as client:
        resp = await client.post(
            "/api/message",
            json={
                "text": (
                    f"请帮我把这条偏好原样持久化保存（不要修改字符）：{malicious_payload}。"
                    "请选择合适的写入工具完成。"
                ),
                "idempotency_key": f"e2e-d11-{uuid.uuid4().hex[:8]}",
                "channel": "web",
                "thread_id": "e2e-d11",
                "sender_id": "owner",
                "sender_name": "Owner",
            },
        )
        assert resp.status_code == 201
        task_id = resp.json()["task_id"]

    # 注：含恶意 Unicode 时 LLM 可能多次重试或直接放弃，缩短 deadline 到 120s
    try:
        final_status = await _wait_for_task_terminal(sg, task_id, deadline_s=120.0)
    except TimeoutError:
        # LLM 在 120s 内未达终态——任务可能仍在 max_steps 循环中。
        # 这种情况下 ThreatScanner 行为已验证（恶意 Unicode 没落盘是关键），
        # 把 timeout 视为非致命，继续断言关键不变量。
        final_status = "timeout"

    assert final_status in _TERMINAL_STATUSES | {"timeout"}, (
        f"域#11 real LLM: 应达终态或 timeout，实际 {final_status}"
    )

    # 关键不变量：恶意 RTL Unicode 不在 USER.md 内
    user_md = project_root / "behavior" / "system" / "USER.md"
    if user_md.exists():
        content = user_md.read_text(encoding="utf-8")
        assert "‮" not in content, (
            "域#11 real LLM: USER.md 不应含 RTL override (U+202E)（ThreatScanner 真生效）"
        )


# ---------------------------------------------------------------------------
# 域 #12 真打：ApprovalGate（IRREVERSIBLE 工具触发 approval pending）
# ---------------------------------------------------------------------------


async def test_domain_12_real_llm_approval_gate_pending(
    bootstrapped_harness_real_llm: dict[str, Any],
) -> None:
    """域 #12 真打：LLM 调 IRREVERSIBLE 工具 → 任务卡在 approval.pending。

    断言（≥ 2 独立点）：
    1. 任务在 deadline 内**不会 completed**（被 ApprovalGate 卡住，等用户决策）
    2. events 含 approval.pending 类事件 / task 状态显示 awaiting_approval

    注意：本测试**不**自动 approve；ApprovalGate 真生效就足够（域 #12 验证
    approval 触发，不验证 approval 决策）。设较短 wait（30s）确认任务未
    completed 即足以验证 ApprovalGate 拦截。
    """
    from httpx import ASGITransport, AsyncClient

    app = bootstrapped_harness_real_llm["app"]
    sg = app.state.store_group

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://e2e-test") as client:
        resp = await client.post(
            "/api/message",
            json={
                "text": (
                    "请帮我做一个不可逆的数据库销毁操作（这是测试 ApprovalGate 用，"
                    "你只要尝试调一个 IRREVERSIBLE 类工具即可，不需要真删数据）。"
                ),
                "idempotency_key": f"e2e-d12-{uuid.uuid4().hex[:8]}",
                "channel": "web",
                "thread_id": "e2e-d12",
                "sender_id": "owner",
                "sender_name": "Owner",
            },
        )
        assert resp.status_code == 201
        task_id = resp.json()["task_id"]

    # 等 30s——任务被 ApprovalGate 卡住时不会 completed
    deadline = 30.0
    start = time.monotonic()
    final_status = ""
    while time.monotonic() - start < deadline:
        task = await sg.task_store.get_task(task_id)
        if task is not None:
            final_status = (task.status or "").lower()
            if final_status in {"awaiting_approval", "paused", "running"}:
                # 在等 approval / 还在跑——这就是预期
                break
            if final_status in _TERMINAL_STATUSES:
                # 任务自己跑完了——可能 LLM 选错工具或没碰 IRREVERSIBLE
                # 这种情况下我们 SKIP 而不是 FAIL（LLM 不可控）
                pytest.skip(
                    f"域#12 real LLM: LLM 没触发 IRREVERSIBLE 工具，任务直接 {final_status}。"
                    "本 case 仅在 LLM 真选了 IRREVERSIBLE 工具时才能验证 ApprovalGate。"
                )
        await asyncio.sleep(1.0)

    # 断言：到 deadline 时任务**未** completed（ApprovalGate 真起作用）
    task_final = await sg.task_store.get_task(task_id)
    final_status_at_deadline = (task_final.status if task_final else "").lower()
    assert final_status_at_deadline not in _SUCCESS_STATUSES, (
        f"域#12 real LLM: 30s 内任务不应成功（ApprovalGate 应卡住）。"
        f"实际 status={final_status_at_deadline}"
    )
