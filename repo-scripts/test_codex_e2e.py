#!/usr/bin/env python3
"""Codex Responses API 端到端验证脚本

通过 HandlerChain 解析凭证 + 路由信息，直接调用 ChatGPT backend API。
验证 JWT OAuth 全链路：CredentialStore → HandlerChain → API 调用。

用法（从 repo 根目录执行）:
    cd octoagent && uv run python ../repo-scripts/test_codex_e2e.py
    cd octoagent && uv run python ../repo-scripts/test_codex_e2e.py --prompt "用 Python 写一个快排"
    cd octoagent && uv run python ../repo-scripts/test_codex_e2e.py --stream
"""

from __future__ import annotations

import argparse
import asyncio
import json
import subprocess
import sys
from pathlib import Path


def _curl_post(
    url: str,
    *,
    headers: dict[str, str],
    json_body: dict,
    stream: bool = False,
) -> str:
    """通过 curl 发送 POST 请求（绕过 Python SSL 问题）"""
    cmd = [
        "curl", "-s", "--max-time", "90",
        "--retry", "3", "--retry-delay", "1", "--retry-all-errors",
        "-X", "POST",
        url,
        "-H", "Content-Type: application/json",
    ]
    for k, v in headers.items():
        cmd.extend(["-H", f"{k}: {v}"])
    cmd.extend(["--data", json.dumps(json_body)])

    if stream:
        # 流式模式：逐行输出（curl retry 在 Popen 模式下不生效，手动重试）
        for attempt in range(3):
            proc = subprocess.Popen(
                cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
            )
            # 检查首字节是否到达（如果 SSL 失败会立即退出）
            import select
            ready, _, _ = select.select([proc.stdout], [], [], 10)
            if ready:
                break  # 连接成功
            proc.kill()
            proc.wait()
            if attempt < 2:
                import time as _time
                _time.sleep(1)
        # 此处 proc 为最后一次尝试的进程
        output_lines = []
        assert proc.stdout is not None
        for line in proc.stdout:
            line = line.strip()
            if not line:
                continue
            # SSE 格式: "data: {...}" 或 "event: ..."
            if line.startswith("data: "):
                data_str = line[6:]
                if data_str == "[DONE]":
                    break
                try:
                    data = json.loads(data_str)
                    # 提取文本增量
                    if data.get("type") == "response.output_text.delta":
                        delta = data.get("delta", "")
                        print(delta, end="", flush=True)
                        output_lines.append(delta)
                except json.JSONDecodeError:
                    pass
            else:
                output_lines.append(line)
        proc.wait()
        print()  # 换行
        return "".join(output_lines)
    else:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
        if result.returncode != 0:
            print(f"[错误] curl 返回码={result.returncode} stderr={result.stderr}", file=sys.stderr)
            if result.stdout:
                print(f"[错误] stdout={result.stdout[:500]}", file=sys.stderr)
            sys.exit(1)
        return result.stdout


async def main() -> None:
    parser = argparse.ArgumentParser(description="Codex Responses API 端到端验证")
    parser.add_argument(
        "--prompt", "-p",
        default="请用一句话介绍你自己。",
        help="发送的提示语",
    )
    parser.add_argument(
        "--model", "-m",
        default="gpt-5.3-codex",
        help="模型名称（默认 gpt-5.3-codex）",
    )
    parser.add_argument(
        "--stream", "-s",
        action="store_true",
        help="启用流式输出",
    )
    parser.add_argument(
        "--reasoning-effort", "-r",
        choices=["none", "low", "medium", "high", "xhigh"],
        default=None,
        help="推理深度级别（none/low/medium/high/xhigh）",
    )
    parser.add_argument(
        "--reasoning-summary",
        choices=["auto", "concise", "detailed"],
        default=None,
        help="推理摘要模式（auto/concise/detailed）",
    )
    parser.add_argument(
        "--profile",
        default="openai-codex-default",
        help="凭证 profile 名称",
    )
    args = parser.parse_args()

    # --- 1. 通过 HandlerChain 解析凭证 + 路由 ---
    from octoagent.provider.auth.chain import HandlerChain
    from octoagent.provider.auth.oauth_provider import BUILTIN_PROVIDERS
    from octoagent.provider.auth.store import CredentialStore

    store_path = Path.home() / ".octoagent" / "auth-profiles.json"
    if not store_path.exists():
        print("[错误] 凭证文件不存在，请先运行 octo init", file=sys.stderr)
        sys.exit(1)

    store = CredentialStore(store_path=store_path)
    chain = HandlerChain(store=store)

    # 注册 PkceOAuthAdapter factory
    config = BUILTIN_PROVIDERS.get("openai-codex")
    if config:
        chain.register_pkce_oauth_factory(
            provider="openai-codex",
            provider_config=config,
            profile_name=args.profile,
        )

    result = await chain.resolve(
        provider="openai-codex",
        profile_name=args.profile,
    )

    print(f"[凭证] provider={result.provider} adapter={result.adapter} source={result.source}")
    print(f"[路由] api_base_url={result.api_base_url}")
    print(f"[路由] extra_headers={list(result.extra_headers.keys())}")

    if result.provider == "echo":
        print("[错误] 未找到有效凭证，请先运行 octo init", file=sys.stderr)
        sys.exit(1)

    # --- 2. 构建 API 请求 ---
    api_base = result.api_base_url or "https://chatgpt.com/backend-api"
    url = f"{api_base}/codex/responses"

    headers = {
        "Authorization": f"Bearer {result.credential_value}",
        **result.extra_headers,
    }

    body = {
        "model": args.model,
        "instructions": "You are a helpful coding assistant.",
        "input": [
            {
                "type": "message",
                "role": "user",
                "content": [
                    {"type": "input_text", "text": args.prompt},
                ],
            },
        ],
        "stream": True,
        "store": False,
    }

    # Responses API reasoning 配置
    if args.reasoning_effort is not None:
        reasoning_param: dict[str, str] = {"effort": args.reasoning_effort}
        if args.reasoning_summary is not None:
            reasoning_param["summary"] = args.reasoning_summary
        body["reasoning"] = reasoning_param

    reasoning_str = args.reasoning_effort or "default"
    print(f"\n[请求] POST {url}")
    print(f"[请求] model={args.model} stream={args.stream} reasoning={reasoning_str}")
    print(f"[请求] prompt: {args.prompt}")
    print("---")

    # --- 3. 调用 API（ChatGPT backend 强制流式） ---
    _curl_post(url, headers=headers, json_body=body, stream=True)


if __name__ == "__main__":
    asyncio.run(main())
