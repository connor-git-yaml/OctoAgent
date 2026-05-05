#!/usr/bin/env python3
"""极简 MCP stdio stub server（F089 L1 e2e 用）。

- 实现 initialize / tools/list / tools/call(echo)，line-delimited JSON-RPC
- ``STUB_DEBUG=1`` 时把每条 stdin/stdout 消息打到 stderr，便于 e2e 诊断
- 文件名前缀下划线 → pytest 不会 collect 它

被 ``test_e2e_mcp_broker.py`` 当 stdio subprocess 拉起，验证
``broker.execute("mcp.<server>.<tool>", ...)`` 完整审计链路。
"""
from __future__ import annotations

import json
import os
import sys
from typing import Any

DEBUG = os.environ.get("STUB_DEBUG") == "1"


def _dbg(msg: str) -> None:
    if DEBUG:
        sys.stderr.write(f"[stub] {msg}\n")
        sys.stderr.flush()


def _send(payload: dict[str, Any]) -> None:
    line = json.dumps(payload, ensure_ascii=False)
    sys.stdout.write(line + "\n")
    sys.stdout.flush()
    _dbg(f"send: {line[:200]}")


def main() -> None:
    _dbg(f"started pid={os.getpid()}")
    for raw in sys.stdin:
        raw = raw.strip()
        if not raw:
            continue
        _dbg(f"recv: {raw[:200]}")
        try:
            req = json.loads(raw)
        except Exception as e:
            _dbg(f"json parse failed: {e}")
            continue

        # Notification: no id field
        if "id" not in req:
            _dbg(f"notification: {req.get('method')}")
            continue

        method = req.get("method", "")
        rid = req.get("id")

        if method == "initialize":
            _send(
                {
                    "jsonrpc": "2.0",
                    "id": rid,
                    "result": {
                        "protocolVersion": "2024-11-05",
                        "capabilities": {"tools": {}},
                        "serverInfo": {"name": "stub", "version": "0.0.1"},
                    },
                }
            )
        elif method == "tools/list":
            _send(
                {
                    "jsonrpc": "2.0",
                    "id": rid,
                    "result": {
                        "tools": [
                            {
                                "name": "echo",
                                "description": "Echo back input",
                                "inputSchema": {
                                    "type": "object",
                                    "properties": {"text": {"type": "string"}},
                                },
                            }
                        ]
                    },
                }
            )
        elif method == "tools/call":
            params = req.get("params") or {}
            tool_name = params.get("name", "")
            args = params.get("arguments") or {}
            text = args.get("text", "")
            _send(
                {
                    "jsonrpc": "2.0",
                    "id": rid,
                    "result": {
                        "content": [{"type": "text", "text": f"echo:{text}"}],
                        "isError": False,
                    },
                }
            )
        else:
            _send(
                {
                    "jsonrpc": "2.0",
                    "id": rid,
                    "error": {"code": -32601, "message": f"method not found: {method}"},
                }
            )


if __name__ == "__main__":
    main()
