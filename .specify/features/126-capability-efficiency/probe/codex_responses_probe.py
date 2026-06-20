"""F126 T120 — codex OAuth responses transport KV-cache 探针（走 OctoAgent ProviderRouter）。

用户拍板：用 ChatGPT Pro codex OAuth 跑一次性 responses 探针（非 benchmark 套件）。
raw usage（含 cached_tokens 若有）由 provider_client.py 临时插桩打到 stderr。
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
from pathlib import Path

from octoagent.provider.provider_router import ProviderRouter

INSTANCE_ROOT = Path(os.path.expanduser("~/.octoagent"))

SYSTEM = "You are a meticulous assistant. " + ("Follow the operating manual section verbatim and never deviate. " * 220)
TOOL_A_BIG = "TOOL_RESULT_A: " + ("row of structured telemetry data field=value status=ok latency=12ms " * 220)
PLACEHOLDER_A = "[已折叠，见 artifact:01PROBEARTIFACT0000000000A（工具 telemetry，原始 18000 字节）]"
MSG_B = "TOOL_RESULT_B: " + ("secondary diagnostic output line " * 40)
MSG_C = "Assistant analysis: proceeding to next step based on the above."
USER_Q = "Given the manual and tool results above, reply with the single word: ACK."


def _history(tool_a: str) -> list[dict]:
    # Chat Completions 格式 history（不含 system，system 走 instructions）
    return [
        {"role": "user", "content": "Begin task. Here is tool A output:"},
        {"role": "assistant", "content": tool_a},
        {"role": "user", "content": "Here is tool B output:"},
        {"role": "assistant", "content": MSG_B},
        {"role": "user", "content": MSG_C + "\n\n" + USER_Q},
    ]


async def main() -> None:
    router = ProviderRouter(project_root=INSTANCE_ROOT)
    try:
        resolved = router.resolve_for_alias("main")
        print(f"resolved: provider={resolved.provider_id} model={resolved.model_name}", file=sys.stderr)
        full = _history(TOOL_A_BIG)
        folded = _history(PLACEHOLDER_A)

        async def one(label: str, hist: list[dict]) -> None:
            print(f"=== {label} ===", file=sys.stderr)
            content, tool_calls, meta = await resolved.client.call(
                instructions=SYSTEM,
                history=hist,
                tools=[],
                model_name=resolved.model_name,
            )
            print(f"{label} normalized token_usage={json.dumps(meta.get('token_usage'))}", file=sys.stderr)

        await one("R1_warm", full)
        await asyncio.sleep(2)
        await one("R2_same", full)
        await one("R3_folded_first", folded)
        await asyncio.sleep(2)
        await one("R4_folded_again", folded)
    finally:
        await router.aclose()


if __name__ == "__main__":
    asyncio.run(main())
