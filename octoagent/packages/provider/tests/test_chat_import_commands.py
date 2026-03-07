from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

from click.testing import CliRunner
from octoagent.provider.dx.cli import main


def _write_jsonl(path: Path, rows: list[dict]) -> None:
    path.write_text(
        "\n".join(json.dumps(row, ensure_ascii=False) for row in rows),
        encoding="utf-8",
    )


def test_import_chats_dry_run_command(tmp_path: Path) -> None:
    imports_dir = tmp_path / "imports"
    imports_dir.mkdir(parents=True, exist_ok=True)
    input_path = imports_dir / "messages.jsonl"
    _write_jsonl(
        input_path,
        [
            {
                "source_message_id": "m1",
                "channel": "wechat_import",
                "thread_id": "project-alpha",
                "sender_id": "alice",
                "timestamp": datetime.now(tz=UTC).isoformat(),
                "text": "hello",
            }
        ],
    )
    runner = CliRunner()

    result = runner.invoke(
        main,
        [
            "import",
            "chats",
            "--input",
            "imports/messages.jsonl",
            "--dry-run",
            "--channel",
            "telegram",
            "--thread-id",
            "override-thread",
        ],
        env={"OCTOAGENT_PROJECT_ROOT": str(tmp_path)},
    )

    assert result.exit_code == 0
    assert "Chat Import Dry Run" in result.output
    assert "scope: chat:telegram:override-thread" in result.output


def test_import_chats_missing_input_returns_exit_2(tmp_path: Path) -> None:
    runner = CliRunner()

    result = runner.invoke(
        main,
        ["import", "chats", "--input", str(tmp_path / "missing.jsonl")],
        env={"OCTOAGENT_PROJECT_ROOT": str(tmp_path)},
    )

    assert result.exit_code == 2
    assert "输入文件不存在" in result.output
