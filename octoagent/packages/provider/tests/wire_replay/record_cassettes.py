"""F139 cassette 录制脚本（真调用，人监督下显式跑；spec D7 / FR-9）。

重录路径（仿 pydantic-ai ``make update-vcr-tests``，文档化于此 + testing-strategy.md）::

    cd <repo>/octoagent
    OCTOAGENT_ALLOW_MODEL_REQUESTS=1 uv run --no-sync python \\
        packages/provider/tests/wire_replay/record_cassettes.py all

    # 单 transport 重录：
    #   ... record_cassettes.py chat        # SiliconFlow（API key，极便宜）
    #   ... record_cassettes.py responses   # openai-codex（订阅 OAuth，勿循环重录）
    #   ... record_cassettes.py anthropic   # 需宿主配置 anthropic provider（现无 →
    #                                       # cassette 为手写 golden，见 spec §2）

重录后必做（顺序固定）：
1. 跑 secret 扫描：``pytest packages/provider/tests/wire_replay/ -k secret``；
2. 人眼 review cassette diff 全文 + ``grep -RE "sk-[A-Za-z0-9_-]{8,}|tskey-|eyJ"``；
3. 更新回放测试的精确断言（脚本尾部打印每个场景的解析摘要，直接誊写）；
4. 重跑回放套件确认全绿。

纪律（spec D7）：
- gate opt-in：必须显式 ``OCTOAGENT_ALLOW_MODEL_REQUESTS=1``，未设置直接退出；
- 调用预算：SiliconFlow ≤ 8 次 / codex OAuth ≤ 4 次（订阅额度，禁循环重录）；
- 录制终端输出可能含 provider 错误回显（与日常跑 octo 同面）——勿粘贴外发；
- cassette 落盘走六道过滤管线（_wire_recorder.CassetteRecorder），扫描不过
  不产出文件（fail-closed）。
"""

from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path
from typing import Any

import httpx

_HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(_HERE))  # 脚本形态直跑：绕开测试包相对 import

import scenarios  # noqa: E402
from _wire_recorder import (  # noqa: E402
    CassetteRecorder,
    RecordingTransport,
)

CASSETTES_DIR = _HERE / "cassettes"
OCTOAGENT_HOME = Path("~/.octoagent").expanduser()

_GATE_ENV = "OCTOAGENT_ALLOW_MODEL_REQUESTS"


def _require_gate() -> None:
    if os.environ.get(_GATE_ENV, "").strip() != "1":
        print(
            f"[refused] 录制=真 LLM 调用，必须显式 {_GATE_ENV}=1 才能跑"
            "（F137 gate 通道③；spec FR-9）。",
        )
        raise SystemExit(78)


def _load_host_env() -> None:
    """加载宿主 ~/.octoagent/.env（SILICONFLOW_API_KEY 等，不覆盖已有 env）。"""
    from octoagent.gateway.services.config.dotenv_loader import load_project_dotenv

    load_project_dotenv(OCTOAGENT_HOME)


def _build_recording_client(recorder: CassetteRecorder) -> httpx.AsyncClient:
    return httpx.AsyncClient(
        timeout=httpx.Timeout(120.0, connect=10.0),
        transport=RecordingTransport(recorder),
    )


def _print_parse_summary(
    scenario_name: str,
    content: str,
    tool_calls: list[dict[str, Any]],
    metadata: dict[str, Any],
) -> None:
    print(f"  content     = {content!r}"[:400])
    print(f"  tool_calls  = {tool_calls!r}"[:400])
    print(
        f"  usage       = {metadata.get('token_usage')!r} model={metadata.get('model_name')!r}",
    )


async def _record_scenario(
    *,
    alias: str,
    scenario_name: str,
    call_kwargs: dict[str, Any],
    filename: str,
    extra_note: str | None = None,
) -> None:
    """按宿主 alias 解析 runtime，注入录制 client，跑一个场景并落盘 cassette。"""
    from octoagent.provider.provider_client import ProviderClient
    from octoagent.provider.provider_router import ProviderRouter

    router = ProviderRouter(project_root=OCTOAGENT_HOME)
    try:
        resolved = router.resolve_for_alias(alias)
        runtime = resolved.client.runtime
        recorder = CassetteRecorder(
            meta={
                "provider_id": runtime.provider_id,
                "transport": runtime.transport.value,
                "model": resolved.model_name,
                "source": "live-recording",
                "scenario": scenario_name,
                **({"note": extra_note} if extra_note else {}),
            },
        )
        # 已知凭证禁串登记（spec D3 第 5 道 b）：resolve 出的现役凭证 + 相关 env。
        auth = await runtime.auth_resolver.resolve()
        recorder.register_resolved_auth(auth)
        recorder.register_forbidden_secret(
            os.environ.get("SILICONFLOW_API_KEY"), label="env:SILICONFLOW_API_KEY"
        )

        http_client = _build_recording_client(recorder)
        try:
            client = ProviderClient(runtime, http_client=http_client)
            print(
                f"[record] {scenario_name} → {runtime.provider_id} "
                f"({runtime.transport.value}, model={resolved.model_name})"
            )
            if scenario_name == "embeddings":
                vectors = await client.embed(
                    model_name=scenarios.EMBED_MODEL,
                    texts=scenarios.EMBED_TEXTS,
                )
                print(f"  vectors     = {len(vectors)} × {len(vectors[0]) if vectors else 0} dims")
            else:
                content, tool_calls, metadata = await client.call(
                    model_name=resolved.model_name,
                    **call_kwargs,
                )
                _print_parse_summary(scenario_name, content, tool_calls, metadata)
        finally:
            await http_client.aclose()

        target = CASSETTES_DIR / filename
        try:
            recorder.dump(target)
        except Exception:
            for ctx in recorder.debug_locate_forbidden(recorder.serialize()):
                print(f"  [scan-hit] {ctx}")
            raise
        print(
            f"  cassette    = {target.relative_to(_HERE)} "
            f"({len(recorder.interactions)} interaction)"
        )

        # U+2028 证据分析（spec §5）
        if scenario_name == "u2028_probe":
            body = recorder.interactions[0].body_text
            raw_hit = "\u2028" in body  # 显式转义（防编辑器吞字符）
            print(f"  [U+2028 探针] wire 上未转义原始字符出现 = {raw_hit}")
            print(
                f"  [U+2028 探针] 未转义 CJK 出现（ensure_ascii=False 证据）= "
                f"{any(ord(ch) > 0x2FFF for ch in body)}"
            )
    finally:
        await router.aclose()


async def _record_chat() -> None:
    embed_model_note = f"embed 模型 {scenarios.EMBED_MODEL}（非流式 embed() 路径）"
    await _record_scenario(
        alias="bench",
        scenario_name="simple_completion",
        call_kwargs=scenarios.CHAT_SIMPLE,
        filename="openai_chat_simple.json",
    )
    await _record_scenario(
        alias="bench",
        scenario_name="tool_call",
        call_kwargs=scenarios.CHAT_TOOL_CALL,
        filename="openai_chat_tool_call.json",
    )
    await _record_scenario(
        alias="bench",
        scenario_name="u2028_probe",
        call_kwargs=scenarios.CHAT_U2028_PROBE,
        filename="openai_chat_u2028_probe.json",
        extra_note="spec §5 U+2028 证据探针：模型被要求原样复读含 U+2028 的串",
    )
    await _record_scenario(
        alias="bench",
        scenario_name="embeddings",
        call_kwargs={},
        filename="openai_chat_embeddings.json",
        extra_note=embed_model_note,
    )


async def _record_responses() -> None:
    await _record_scenario(
        alias="main",
        scenario_name="simple_completion",
        call_kwargs=scenarios.RESPONSES_SIMPLE,
        filename="openai_responses_simple.json",
    )
    await _record_scenario(
        alias="main",
        scenario_name="tool_call",
        call_kwargs=scenarios.RESPONSES_TOOL_CALL,
        filename="openai_responses_tool_call.json",
    )


async def _record_anthropic() -> None:
    """anthropic_messages 真录（需宿主配置 anthropic provider 条目 + 有效凭证）。

    2026-07 现状：宿主 octoagent.yaml 无 anthropic provider（auth-profiles 中的
    anthropic-claude-default access_token 判定 stale）→ 仓内 cassette 为手写
    golden（meta.source=handwritten-golden）。未来拿到凭证后：在 octoagent.yaml
    加 provider + alias（如 claude），把下面 alias 换掉并跑本分支替换 golden。
    """
    print(
        "[skip] 宿主未配置 anthropic provider——仓内 anthropic cassette 为手写 "
        "golden（spec §2）。拿到凭证后按本函数 docstring 重录替换。",
    )


async def _main() -> None:
    _require_gate()
    _load_host_env()
    target = sys.argv[1] if len(sys.argv) > 1 else "all"
    if target not in {"all", "chat", "responses", "anthropic"}:
        print(f"用法: record_cassettes.py [all|chat|responses|anthropic]（收到 {target!r}）")
        raise SystemExit(64)
    if target in {"all", "chat"}:
        await _record_chat()
    if target in {"all", "responses"}:
        await _record_responses()
    if target == "anthropic":
        await _record_anthropic()
    print(
        "[done] 重录后必做步骤见模块 docstring（secret 扫描 → 人眼 review → "
        "更新回放断言 → 重跑套件）。"
    )


if __name__ == "__main__":
    asyncio.run(_main())
