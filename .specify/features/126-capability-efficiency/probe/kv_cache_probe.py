"""F126 项2 T120 — KV-cache 实测探针（chat + responses transport）。

核心问题：在 history 中把一条**旧** tool 消息的 content 改写成确定性短占位，
是否使该消息**之后**的前缀 cache 失效？该消息**之前**的前缀是否仍命中？

测法（依赖 OpenAI 自动 prompt caching ≥1024 token 前缀 + usage.cached_tokens）：
- 构造长会话：[system 长稳定块] + [msgA 大 tool 结果] + [msgB] + [msgC] + [user 问]
- R1 预热（写 cache）→ R2 同样请求（应高 cached_tokens，证明前缀已缓存）
- R3 把 msgA content 改写成短确定性占位（其余不动）→ 测 cached_tokens：
    若降到"仅 msgA 之前的 system 前缀长度"附近 = 证明改写 msgA 让其后前缀失效（预期）。
- R4 再发一次 R3 的（已折叠）会话 → cached_tokens 应回升（证明折叠版成新稳定前缀，
    一次性 miss 后重新缓存 = 确定性占位使前缀单调收敛）。

key 从环境读取（OPENAI_API_KEY），不打印 key。仅打印 token 用量与 cached_tokens。
"""

from __future__ import annotations

import json
import os
import sys
import time
import urllib.request

API_KEY = os.environ.get("PROBE_API_KEY", os.environ.get("OPENAI_API_KEY", "")).strip()
MODEL = os.environ.get("PROBE_MODEL", "gpt-4o-mini")  # 便宜且支持自动缓存
BASE = os.environ.get("PROBE_BASE", "https://api.openai.com/v1")


def _post(path: str, body: dict) -> dict:
    req = urllib.request.Request(
        f"{BASE}{path}",
        data=json.dumps(body).encode(),
        headers={
            "Authorization": f"Bearer {API_KEY}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=120) as resp:
        return json.loads(resp.read())


# ---- 构造长稳定前缀（>1024 token 才会自动缓存）----
def _big(filler: str, n: int) -> str:
    return (filler + " ") * n


SYSTEM = "You are a meticulous assistant. " + _big(
    "Follow the operating manual section verbatim and never deviate.", 220
)
TOOL_A_BIG = "TOOL_RESULT_A: " + _big(
    "row of structured telemetry data field=value status=ok latency=12ms", 220
)
PLACEHOLDER_A = "[已折叠，见 artifact:01PROBEARTIFACT0000000000A（工具 telemetry，原始 18000 字节）]"
MSG_B = "TOOL_RESULT_B: " + _big("secondary diagnostic output line", 40)
MSG_C = "Assistant analysis: proceeding to next step based on the above."
USER_Q = "Given the manual and tool results above, reply with the single word: ACK."


def _chat_messages(tool_a_content: str) -> list[dict]:
    return [
        {"role": "system", "content": SYSTEM},
        {"role": "user", "content": "Begin task. Here is tool A output:"},
        {"role": "assistant", "content": tool_a_content},
        {"role": "user", "content": "Here is tool B output:"},
        {"role": "assistant", "content": MSG_B},
        {"role": "user", "content": MSG_C + "\n\n" + USER_Q},
    ]


def _cached_chat(resp: dict) -> tuple[int, int]:
    u = resp.get("usage", {})
    details = u.get("prompt_tokens_details", {}) or {}
    # OpenAI: prompt_tokens_details.cached_tokens；DeepSeek: prompt_cache_hit_tokens
    cached = details.get("cached_tokens")
    if cached is None:
        cached = u.get("prompt_cache_hit_tokens", 0)
    return u.get("prompt_tokens", 0), cached or 0


def run_chat() -> dict:
    out = {}
    full = _chat_messages(TOOL_A_BIG)
    folded = _chat_messages(PLACEHOLDER_A)
    # R1 预热
    r1 = _post("/chat/completions", {"model": MODEL, "messages": full, "max_tokens": 5, "temperature": 0})
    out["R1_warm"] = _cached_chat(r1)
    time.sleep(2)
    # R2 同 → 应命中
    r2 = _post("/chat/completions", {"model": MODEL, "messages": full, "max_tokens": 5, "temperature": 0})
    out["R2_same"] = _cached_chat(r2)
    # R3 改写 msgA 为占位 → 测其后前缀是否失效
    r3 = _post("/chat/completions", {"model": MODEL, "messages": folded, "max_tokens": 5, "temperature": 0})
    out["R3_folded_first"] = _cached_chat(r3)
    time.sleep(2)
    # R4 再发折叠版 → 应回升（折叠版成新稳定前缀）
    r4 = _post("/chat/completions", {"model": MODEL, "messages": folded, "max_tokens": 5, "temperature": 0})
    out["R4_folded_again"] = _cached_chat(r4)
    return out


def _responses_input(tool_a_content: str) -> list[dict]:
    # Responses API 用 input 数组（role+content）
    return [
        {"role": "system", "content": SYSTEM},
        {"role": "user", "content": "Begin task. Here is tool A output:"},
        {"role": "assistant", "content": tool_a_content},
        {"role": "user", "content": "Here is tool B output:"},
        {"role": "assistant", "content": MSG_B},
        {"role": "user", "content": MSG_C + "\n\n" + USER_Q},
    ]


def _cached_resp(resp: dict) -> tuple[int, int]:
    u = resp.get("usage", {})
    details = u.get("input_tokens_details", {}) or {}
    return u.get("input_tokens", 0), details.get("cached_tokens", 0)


def run_responses() -> dict:
    out = {}
    full = _responses_input(TOOL_A_BIG)
    folded = _responses_input(PLACEHOLDER_A)
    body = lambda inp: {"model": MODEL, "input": inp, "max_output_tokens": 16, "store": False}
    r1 = _post("/responses", body(full))
    out["R1_warm"] = _cached_resp(r1)
    time.sleep(2)
    r2 = _post("/responses", body(full))
    out["R2_same"] = _cached_resp(r2)
    r3 = _post("/responses", body(folded))
    out["R3_folded_first"] = _cached_resp(r3)
    time.sleep(2)
    r4 = _post("/responses", body(folded))
    out["R4_folded_again"] = _cached_resp(r4)
    return out


# ---- anthropic transport（Messages API + cache_control）----
ANTHROPIC_BASE = "https://api.anthropic.com/v1"


def _anthropic_post(body: dict, key: str) -> dict:
    req = urllib.request.Request(
        f"{ANTHROPIC_BASE}/messages",
        data=json.dumps(body).encode(),
        headers={
            "x-api-key": key,
            "anthropic-version": "2023-06-01",
            "content-type": "application/json",
        },
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=120) as resp:
        return json.loads(resp.read())


def _anthropic_msgs(tool_a_content: str) -> tuple[list, list]:
    # system 作为可缓存块（末尾 cache_control breakpoint）；msgA 大块亦设 breakpoint。
    system = [
        {"type": "text", "text": SYSTEM, "cache_control": {"type": "ephemeral"}},
    ]
    messages = [
        {"role": "user", "content": "Begin task. Here is tool A output:"},
        {
            "role": "assistant",
            "content": [
                {"type": "text", "text": tool_a_content, "cache_control": {"type": "ephemeral"}}
            ],
        },
        {"role": "user", "content": "Here is tool B output: " + MSG_B},
        {"role": "assistant", "content": MSG_C},
        {"role": "user", "content": USER_Q},
    ]
    return system, messages


def _cached_anthropic(resp: dict) -> tuple[int, int]:
    u = resp.get("usage", {})
    # cache_read_input_tokens = 命中缓存的 token
    read = u.get("cache_read_input_tokens", 0)
    created = u.get("cache_creation_input_tokens", 0)
    inp = u.get("input_tokens", 0)
    return inp + read + created, read


def run_anthropic(model: str, key: str) -> dict:
    out = {}
    sys_full, msgs_full = _anthropic_msgs(TOOL_A_BIG)
    sys_fold, msgs_fold = _anthropic_msgs(PLACEHOLDER_A)
    body = lambda s, m: {"model": model, "max_tokens": 8, "system": s, "messages": m}
    r1 = _anthropic_post(body(sys_full, msgs_full), key)
    out["R1_warm"] = _cached_anthropic(r1)
    time.sleep(2)
    r2 = _anthropic_post(body(sys_full, msgs_full), key)
    out["R2_same"] = _cached_anthropic(r2)
    r3 = _anthropic_post(body(sys_fold, msgs_fold), key)
    out["R3_folded_first"] = _cached_anthropic(r3)
    time.sleep(2)
    r4 = _anthropic_post(body(sys_fold, msgs_fold), key)
    out["R4_folded_again"] = _cached_anthropic(r4)
    return out


def _cc_messages(tool_a_content: str) -> list[dict]:
    """chat completions wire + Anthropic-style cache_control breakpoints（OpenRouter→Claude）。"""
    return [
        {"role": "system", "content": [
            {"type": "text", "text": SYSTEM, "cache_control": {"type": "ephemeral"}}]},
        {"role": "user", "content": "Begin task. Here is tool A output:"},
        {"role": "assistant", "content": [
            {"type": "text", "text": tool_a_content, "cache_control": {"type": "ephemeral"}}]},
        {"role": "user", "content": "Here is tool B output: " + MSG_B},
        {"role": "assistant", "content": MSG_C},
        {"role": "user", "content": USER_Q},
    ]


def run_openrouter_claude() -> dict:
    """OpenRouter→Claude（chat wire + cache_control），打印原始 usage 便于看缓存字段。"""
    out = {}
    full = _cc_messages(TOOL_A_BIG)
    folded = _cc_messages(PLACEHOLDER_A)
    body = lambda m: {"model": MODEL, "messages": m, "max_tokens": 8, "temperature": 0,
                      "usage": {"include": True}}
    for label, msgs, pause in [
        ("R1_warm", full, 2), ("R2_same", full, 0),
        ("R3_folded_first", folded, 2), ("R4_folded_again", folded, 0),
    ]:
        r = _post("/chat/completions", body(msgs))
        out[label] = r.get("usage", {})
        if pause:
            time.sleep(pause)
    return out


if __name__ == "__main__":
    which = sys.argv[1] if len(sys.argv) > 1 else "both"
    if which == "openrouter":
        result = {"model": MODEL, "openrouter_claude_raw_usage": {}}
        try:
            result["openrouter_claude_raw_usage"] = run_openrouter_claude()
        except Exception as e:
            result["openrouter_error"] = f"{type(e).__name__}: {e}"
        print(json.dumps(result, ensure_ascii=False, indent=2))
        sys.exit(0)
    result = {"model": MODEL}
    if which in ("chat", "both", "openai"):
        if not API_KEY:
            result["chat_error"] = "NO_KEY"
        else:
            try:
                result["chat"] = run_chat()
            except Exception as e:
                result["chat_error"] = f"{type(e).__name__}: {e}"
    if which in ("responses", "both", "openai"):
        if not API_KEY:
            result["responses_error"] = "NO_KEY"
        else:
            try:
                result["responses"] = run_responses()
            except Exception as e:
                result["responses_error"] = f"{type(e).__name__}: {e}"
    if which == "anthropic":
        akey = os.environ.get("ANTHROPIC_API_KEY", "").strip()
        amodel = os.environ.get("PROBE_ANTHROPIC_MODEL", "claude-haiku-4-5-20251001")
        result = {"model": amodel}
        if not akey:
            result["anthropic_error"] = "NO_KEY"
        else:
            try:
                result["anthropic"] = run_anthropic(amodel, akey)
            except Exception as e:
                result["anthropic_error"] = f"{type(e).__name__}: {e}"
    print(json.dumps(result, ensure_ascii=False, indent=2))
