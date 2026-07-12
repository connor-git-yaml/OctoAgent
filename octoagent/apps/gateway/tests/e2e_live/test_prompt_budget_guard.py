"""F142 件2：prompt token 预算护栏（agent-zero 范式，L3 确定性零真 LLM）。

治理对象：master 此前**没有任何** system prompt 体积护栏（grep 0 hit）——
``_fit_prompt_budget`` 是运行时**裁剪**（超预算静默修剪，prompt creep 只挤占
conversation 预算，CI 不报警）。F095 envelope 过滤 bug、F126 KV-cache 稳定前缀
都证明 prompt 表面漂移是真风险。本文件把「全量真实组装的 system 面」钉上硬上限
+ 关键指令短语在场 + 退役内容负向扫描，成本一个文件、常驻护栏
（对照 agent-zero ``test_default_prompt_budget.py``：≤10000 硬上限 + 短语在场 +
负向全库扫描）。

机制：F138 harness 真 bootstrap（全 11 段，behavior 走
``packages/core/behavior_templates/`` 默认模板——``resolve_behavior_workspace``
source_chain ``default_behavior_templates``）→ 包一层 recording ``llm_service.call``
→ ``POST /api/message`` 驱动**真 chat 主路径**（task_runner →
``task_service._build_task_context`` → ``build_task_context`` 全量组装）→ 捕获
``compiled_context.messages``（task_service.py:748 原样传入 llm_service）→ 用
**生产同源估算器** ``estimate_messages_tokens`` 度量 system 面。零真 LLM：
组装发生在 ``llm_service.call`` 之前，recording 包装记录后直接返回 canned
应答收尾任务（不委托原实现——F137 gate=deny 下真调用尝试会让任务 FAILED，
探针轮实证）；F137 deny 闸全程在位兜底。

spec 件2 机制偏离归档：spec 原写「捕获脚本脑收到的 conversation_messages」，
实施改为在 ``llm_service.call`` 捕获——同一份 ``compiled_context.messages``、
更上游更简单，且避免 scripted client 被 chat 主路径的 tool-selection 辅助调用
乱序消费的不确定性。

cap 校准记录（2026-07-12 实测收口，spec 件2「cap 按实测值收口 + ~15% 余量」）：
- 估算器算法：``tokenizer``（tiktoken 0.12 cl100k_base——**已在 uv.lock 锁定**，
  本地与 CI ``uv sync`` 环境一致；若估算器降级到 ``cjk_aware``（tiktoken 缺失），
  CJK 计数会**变低**，cap 仍安全但灵敏度下降——失败信息里带算法名辅助判断）
- system 面实测：2 条 system messages / 16636 chars / **8938 tokens**
  → cap 10300（+15.2%）
- 工具 schema 面实测：68 tools / 33473 chars / **11253 tokens**
  → cap 13000（+15.5%）
"""

from __future__ import annotations

import json
import uuid
from pathlib import Path
from typing import Any

import pytest

pytestmark = [pytest.mark.e2e_scripted, pytest.mark.e2e_live]

# ---------------------------------------------------------------------------
# 硬上限（实测收口，见模块 docstring 校准记录）
# ---------------------------------------------------------------------------
SYSTEM_PROMPT_TOKEN_CAP = 10300  # 实测 8938（tokenizer）× ~1.15
TOOL_SCHEMA_TOKEN_CAP = 13000  # 实测 11253（tokenizer，68 工具）× ~1.15

# 关键指令短语（承重语义在场断言）：从 2026-07-12 组装产物实测挑选，每条守一个
# 不同注入层——丢失即说明 envelope 过滤或模板漂移吃掉了治理指令（F095 bug 形态：
# 当年 envelope AND 子句剥离 IDENTITY，模板渲染了但 LLM 永远看不到）。
_REQUIRED_SYSTEM_PHRASES: list[str] = [
    # AGENTS.md 协作规则（H1/H3 决策指令，BehaviorSystem role 层）
    "先理解目标，再决定是直接处理、委派给 Worker，还是创建 Subagent",
    # AGENTS.md 治理规则（Constitution #4 的 LLM 可见表述）
    "高风险动作必须遵守 Plan → Approve → Execute",
    # TOOLS.md 指南在场（tool_boundary 层）
    "## 工具选择优先级",
    # AmbientRuntime block（F108b Block 2，时间/场地事实层）
    "AmbientRuntime:",
    # MemoryRuntime block（recall runtime 提示层）
    "MemoryRuntime:",
]

# 退役内容负向扫描目标。注意 ``.env.litellm`` **不在列**：它是存活文件名化石
# （SecretService/backup_service/path_policy 现仍真读写该名字），其改名是独立
# 迁移候选（F142 completion-report 归档），bare "litellm" 会误伤。
_RETIRED_CONTENT_MARKERS = [
    "LiteLLM Proxy",  # F081 退役子系统
    "litellm-config.yaml",  # F081 退役配置文件
    "BootstrapSession",  # F084 退役状态机
    "bootstrap_orchestrator",  # F084 退役编排器
    "UserMdRenderer",  # F084 退役渲染器
]


@pytest.fixture
async def budget_harness(tmp_path: Path):
    """真 bootstrap harness + recording llm_service（照 F138 keystone 范式）。"""
    from fastapi import FastAPI
    from octoagent.gateway.harness.octo_harness import OctoHarness

    e2e_root = tmp_path / "octoagent_budget_root"
    data_dir = e2e_root / "data"
    mcp_servers_dir = e2e_root / "mcp-servers"
    e2e_root.mkdir(parents=True, exist_ok=True)
    data_dir.mkdir(parents=True, exist_ok=True)
    mcp_servers_dir.mkdir(parents=True, exist_ok=True)
    (e2e_root / "behavior" / "system").mkdir(parents=True, exist_ok=True)

    fixtures_root = (
        Path(__file__).resolve().parents[4] / "tests" / "fixtures" / "local-instance"
    )
    if (fixtures_root / "octoagent.yaml.template").exists():
        from apps.gateway.tests.e2e_live.helpers.factories import (
            copy_local_instance_template,
        )

        copy_local_instance_template(fixtures_root, e2e_root)

    from octoagent.provider.auth.store import CredentialStore

    harness = OctoHarness(
        project_root=e2e_root,
        credential_store=CredentialStore(
            store_path=e2e_root / "creds" / "auth-profiles.json"
        ),
        mcp_servers_dir=mcp_servers_dir,
        data_dir=data_dir,
    )
    app = FastAPI()
    await harness.bootstrap(app)
    harness.commit_to_app(app)

    # 手动挂 message 路由（harness.commit_to_app 只给裸 app；照 smoke P4
    # bootstrapped_harness_real_llm 先例带 front_door 保护——fixture 模板
    # octoagent.yaml 配 loopback 模式，ASGITransport client.host=testclient
    # ∈ _LOOPBACK_HOSTS 自动通过）。
    from fastapi import Depends
    from octoagent.gateway.deps import require_front_door_access
    from octoagent.gateway.routes import message

    protected = [Depends(require_front_door_access)]
    app.include_router(message.router, tags=["message"], dependencies=protected)

    # recording 包装：捕获 task_service 传入的 compiled_context.messages（全量
    # 真实组装产物——组装发生在 llm_service.call **之前**，度量面完整），随后
    # 返回 canned 应答收尾任务。不委托原实现：F137 gate=deny 下真调用尝试会
    # 让任务 FAILED（探针轮实证），canned 返回既零 LLM 机器又保住 SUCCEEDED
    # 终态断言（照 test_task_service_context_integration.RecordingLLMService
    # 范式）。
    from octoagent.provider.models import ModelCallResult, TokenUsage

    llm_service = app.state.llm_service
    original_call = llm_service.call
    recorded: list[dict[str, Any]] = []

    async def _recording_call(
        prompt_or_messages, model_alias: str | None = None, **kwargs: Any
    ):
        recorded.append(
            {"prompt_or_messages": prompt_or_messages, "kwargs": dict(kwargs)}
        )
        return ModelCallResult(
            content="预算护栏探针应答",
            model_alias=model_alias or "main",
            model_name="budget-probe",
            provider="budget-probe",
            duration_ms=1,
            token_usage=TokenUsage(
                prompt_tokens=1, completion_tokens=1, total_tokens=2
            ),
            cost_usd=0.0,
            cost_unavailable=False,
            is_fallback=False,
            fallback_reason="",
        )

    llm_service.call = _recording_call  # type: ignore[method-assign]

    yield {
        "harness": harness,
        "app": app,
        "project_root": e2e_root,
        "recorded": recorded,
    }

    llm_service.call = original_call  # type: ignore[method-assign]
    await harness.shutdown(app)


async def _drive_chat_and_capture_system_messages(
    budget_harness: dict[str, Any],
) -> list[dict[str, str]]:
    """POST /api/message 驱动真 chat 主路径，返回主调用的 system-role messages。"""
    import asyncio

    from httpx import ASGITransport, AsyncClient

    app = budget_harness["app"]
    recorded = budget_harness["recorded"]
    sg = app.state.store_group

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://budget-guard"
    ) as client:
        resp = await client.post(
            "/api/message",
            json={
                "text": "帮我看一下今天的日程安排",
                "idempotency_key": f"budget-guard-{uuid.uuid4().hex[:8]}",
                "channel": "web",
                "thread_id": "budget-guard",
                "sender_id": "owner",
                "sender_name": "Owner",
            },
        )
        assert resp.status_code == 201, resp.text
        task_id = resp.json()["task_id"]

    # 轮询任务至终态（Echo 路径应 SUCCEEDED；deadline 兜底防挂）
    terminal = {"SUCCEEDED", "FAILED", "CANCELLED"}
    deadline = asyncio.get_running_loop().time() + 60.0
    status = ""
    while asyncio.get_running_loop().time() < deadline:
        task = await sg.task_store.get_task(task_id)
        status = str(task.status.value if task is not None else "")
        if status in terminal:
            break
        await asyncio.sleep(0.05)
    assert status == "SUCCEEDED", f"chat 主路径应 Echo 收尾成功，实际 {status}"

    # 主调用 = prompt_or_messages 为 list 且含 system role 的那次（过滤掉
    # 内部辅助调用；按捕获顺序取第一条主调用）
    for call in recorded:
        pom = call["prompt_or_messages"]
        if isinstance(pom, list):
            system_msgs = [
                m for m in pom if str(m.get("role", "")).lower() == "system"
            ]
            if system_msgs:
                return system_msgs
    raise AssertionError(
        f"未捕获到含 system blocks 的主 LLM 调用；recorded={len(recorded)} 条"
    )


async def test_full_chat_system_prompt_within_hard_token_cap(
    budget_harness: dict[str, Any],
) -> None:
    """AC-4 主断言：全量真实组装的 system 面 token ≤ 硬 cap。

    cap 击穿 = prompt creep 溢出到了看不见的地方（运行时裁剪只会静默挤占
    conversation 预算）——先看是谁把 system 面撑大，再决定是收内容还是抬 cap
    （抬 cap 必须在本文件留下新校准记录）。
    """
    from octoagent.gateway.services.context_compaction import (
        estimate_messages_tokens,
        estimation_method,
    )

    system_msgs = await _drive_chat_and_capture_system_messages(budget_harness)
    measured = estimate_messages_tokens(system_msgs)
    algorithm = estimation_method()
    joined = "\n\n".join(str(m.get("content", "")) for m in system_msgs)
    print(
        f"\n[budget-probe] system blocks: {len(system_msgs)} 条 / "
        f"{len(joined)} chars / {measured} tokens (algorithm={algorithm})"
    )

    assert measured <= SYSTEM_PROMPT_TOKEN_CAP, (
        f"system 面 {measured} tokens（algorithm={algorithm}，{len(system_msgs)} 条 "
        f"system messages）超过硬上限 {SYSTEM_PROMPT_TOKEN_CAP}。"
        "若为有意扩充：更新 cap 并在模块 docstring 追加新校准记录；"
        "若非有意：找出把 system 面撑大的注入源（BehaviorPack / snapshot / "
        "runtime hints / memory block）"
    )


async def test_full_chat_system_prompt_contains_key_instruction_phrases(
    budget_harness: dict[str, Any],
) -> None:
    """AC-4 短语在场：承重治理指令必须出现在组装产物里（F095 形态防复发——
    当年 envelope AND 子句剥离 IDENTITY，模板渲染了但 LLM 永远看不到）。"""
    system_msgs = await _drive_chat_and_capture_system_messages(budget_harness)
    joined = "\n\n".join(str(m.get("content", "")) for m in system_msgs)

    missing = [p for p in _REQUIRED_SYSTEM_PHRASES if p not in joined]
    assert not missing, (
        f"关键指令短语缺席 system 面：{missing}——envelope 过滤/模板漂移吃掉了"
        "治理指令，或短语本身被改写（后者需同步更新本清单）"
    )


async def test_full_chat_system_prompt_free_of_retired_content(
    budget_harness: dict[str, Any],
) -> None:
    """AC-4 负向（组装产物半边）：退役子系统文案不得复活在 LLM 可见面。"""
    system_msgs = await _drive_chat_and_capture_system_messages(budget_harness)
    joined = "\n\n".join(str(m.get("content", "")) for m in system_msgs)

    hits = [m for m in _RETIRED_CONTENT_MARKERS if m in joined]
    assert not hits, f"退役内容出现在组装后的 system 面：{hits}"


async def test_tool_schema_wire_surface_within_hard_token_cap(
    budget_harness: dict[str, Any],
) -> None:
    """AC-4 工具 schema 面：全量注册工具按 wire 同构 payload（OpenAI 嵌套格式，
    与 ``provider_model_client._get_tool_schemas`` 同形）度量 ≤ 硬 cap。

    这一面不进 system 文本但同样吃上下文预算——全量工具（2026-07-12 实测 68 个）
    的 schema 膨胀此前无人
    看守（F126 只治了 KV-cache 前缀稳定性，不治体积）。
    """
    from octoagent.gateway.services.context_compaction import (
        estimate_text_tokens,
        estimation_method,
    )

    app = budget_harness["app"]
    tools = await app.state.tool_broker.discover()
    assert len(tools) >= 40, (
        f"工具面 sanity：注册工具应 ≥ 40（实际 {len(tools)}）——数量骤降说明"
        "discover 面残缺，cap 断言将失去意义"
    )

    wire_payload = [
        {
            "type": "function",
            "function": {
                "name": t.name,
                "description": t.description,
                "parameters": t.parameters_json_schema,
            },
        }
        for t in tools
    ]
    serialized = json.dumps(wire_payload, ensure_ascii=False)
    measured = estimate_text_tokens(serialized)
    print(
        f"\n[budget-probe] tool schemas: {len(tools)} tools / "
        f"{len(serialized)} chars / {measured} tokens "
        f"(algorithm={estimation_method()})"
    )

    assert measured <= TOOL_SCHEMA_TOKEN_CAP, (
        f"工具 schema 面 {measured} tokens（{len(tools)} 工具）超过硬上限 "
        f"{TOOL_SCHEMA_TOKEN_CAP}。工具新增/schema 膨胀需有意识地付出预算——"
        "更新 cap 前先确认膨胀源（新工具？某工具 schema 巨型化？）并留校准记录"
    )


def test_prompt_template_library_free_of_retired_content() -> None:
    """AC-4 负向（模板库半边，agent-zero 全库扫描形态）：默认行为模板源文件
    不得含退役子系统文案。不需要 harness（纯文件扫描，L4 速度）。"""
    import octoagent.core.behavior_templates as templates_pkg

    templates_dir = Path(templates_pkg.__file__).parent
    md_files = sorted(templates_dir.glob("*.md"))
    assert md_files, f"行为模板目录为空？{templates_dir}"

    offenders: list[str] = []
    for f in md_files:
        text = f.read_text(encoding="utf-8")
        for marker in _RETIRED_CONTENT_MARKERS:
            if marker in text:
                offenders.append(f"{f.name}: {marker}")
    assert not offenders, f"行为模板库含退役内容：{offenders}"
