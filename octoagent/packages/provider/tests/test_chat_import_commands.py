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


def _write_wechat_export(path: Path, media_root: Path) -> None:
    media_root.mkdir(parents=True, exist_ok=True)
    (media_root / "image-1.jpg").write_bytes(b"jpeg")
    payload = {
        "account": {"label": "Connor"},
        "conversations": [
            {
                "conversation_key": "team-alpha",
                "label": "Team Alpha",
                "messages": [
                    {
                        "id": "wx-1",
                        "cursor": "cursor-1",
                        "sender_id": "alice",
                        "sender_name": "Alice",
                        "timestamp": datetime.now(tz=UTC).isoformat(),
                        "text": "hello wechat import",
                        "attachments": [
                            {
                                "path": "image-1.jpg",
                                "filename": "image-1.jpg",
                                "mime": "image/jpeg",
                            }
                        ],
                    }
                ],
            }
        ],
    }
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


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


def test_import_workbench_cli_flow(tmp_path: Path) -> None:
    exports_dir = tmp_path / "exports"
    media_root = exports_dir / "media"
    export_path = exports_dir / "wechat.json"
    exports_dir.mkdir(parents=True, exist_ok=True)
    _write_wechat_export(export_path, media_root)

    runner = CliRunner()
    env = {"OCTOAGENT_PROJECT_ROOT": str(tmp_path)}

    detect = runner.invoke(
        main,
        [
            "import",
            "detect",
            "--source-type",
            "wechat",
            "--input",
            "exports/wechat.json",
            "--media-root",
            "exports/media",
            "--format-hint",
            "json",
        ],
        env=env,
    )

    assert detect.exit_code == 0
    assert "Import Source Detected" in detect.output
    source_line = next(
        line for line in detect.output.splitlines() if line.strip().startswith("source_id:")
    )
    source_id = source_line.split(":", 1)[1].strip()
    assert source_id

    mapping_save = runner.invoke(
        main,
        [
            "import",
            "mapping-save",
            "--source-id",
            source_id,
        ],
        env=env,
    )

    assert mapping_save.exit_code == 0
    assert "Import Mapping Saved" in mapping_save.output

    preview = runner.invoke(
        main,
        [
            "import",
            "preview",
            "--source-id",
            source_id,
        ],
        env=env,
    )

    assert preview.exit_code == 0
    assert "Import Preview" in preview.output
    assert "ready_to_run" in preview.output

    run = runner.invoke(
        main,
        [
            "import",
            "run",
            "--source-id",
            source_id,
        ],
        env=env,
    )

    assert run.exit_code == 0
    assert "Import Run" in run.output
    assert "completed" in run.output
