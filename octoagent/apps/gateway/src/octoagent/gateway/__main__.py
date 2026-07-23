"""OctoAgent Gateway 唯一生产 module entry。"""

from __future__ import annotations

import argparse
import importlib
import os
import sys
from collections.abc import Sequence
from dataclasses import dataclass

USAGE_ERROR_EXIT_CODE = 64
CONFIG_ERROR_EXIT_CODE = 78


class GatewayArgumentParser(argparse.ArgumentParser):
    """把命令行使用错误稳定映射为 EX_USAGE。"""

    def error(self, message: str) -> None:
        self.print_usage(sys.stderr)
        self.exit(USAGE_ERROR_EXIT_CODE, f"GATEWAY_USAGE_INVALID: {message}\n")


@dataclass(frozen=True, slots=True)
class GatewayStartupOptions:
    host: str
    port: int


def _port(value: str) -> int:
    try:
        port = int(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("port must be an integer") from exc
    if not 1 <= port <= 65535:
        raise argparse.ArgumentTypeError("port must be between 1 and 65535")
    return port


def _option_count(argv: Sequence[str], option: str) -> int:
    return sum(token == option or token.startswith(f"{option}=") for token in argv)


def build_parser() -> GatewayArgumentParser:
    parser = GatewayArgumentParser(prog="python -m octoagent.gateway")
    parser.add_argument(
        "--host",
        default=os.environ.get("OCTOAGENT_HOST", "127.0.0.1"),
    )
    parser.add_argument(
        "--port",
        type=_port,
        default=_port(
            os.environ.get(
                "OCTOAGENT_PORT",
                os.environ.get("OCTOAGENT_GATEWAY_PORT", "8000"),
            )
        ),
    )
    return parser


def parse_cli_args(argv: Sequence[str] | None = None) -> GatewayStartupOptions:
    arguments = list(sys.argv[1:] if argv is None else argv)
    parser = build_parser()
    for option in ("--host", "--port"):
        if _option_count(arguments, option) > 1:
            parser.error(f"duplicate option: {option}")
    namespace = parser.parse_args(arguments)
    host = str(namespace.host).strip()
    if not host:
        parser.error("host must not be empty")
    return GatewayStartupOptions(host=host, port=namespace.port)


def main(argv: Sequence[str] | None = None) -> int:
    options = parse_cli_args(argv)
    os.environ["OCTOAGENT_HOST"] = options.host
    os.environ["OCTOAGENT_PORT"] = str(options.port)
    os.environ["OCTOAGENT_GATEWAY_PORT"] = str(options.port)
    try:
        gateway_main = importlib.import_module("octoagent.gateway.main")
    except SystemExit:
        raise
    except Exception as exc:
        error_code = getattr(exc, "error_code", None)
        if error_code in {
            "GATEWAY_RUNTIME_CONFIG_INVALID",
            "GATEWAY_SECURITY_CONFIG_INVALID",
        }:
            print(f"{error_code}: {exc}", file=sys.stderr, flush=True)
            return CONFIG_ERROR_EXIT_CODE
        raise

    uvicorn = importlib.import_module("uvicorn")
    uvicorn.run(gateway_main.app, host=options.host, port=options.port)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
