from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from octoagent.core.models import ProjectBindingType
from octoagent.core.store import create_store_group
from octoagent.provider.dx.import_workbench_models import ImportRunStatus
from octoagent.provider.dx.import_workbench_service import (
    ImportWorkbenchError,
    ImportWorkbenchService,
)


def _write_wechat_export(
    path: Path,
    media_root: Path,
    *,
    include_followup: bool = False,
    conversation_key: str = "release-room",
    label: str = "Release Room",
) -> None:
    started_at = datetime.now(tz=UTC)
    payload = {
        "account": {"label": "Connor"},
        "conversations": [
            {
                "conversation_key": conversation_key,
                "label": label,
                "messages": [
                    {
                        "id": "wx-1",
                        "cursor": "cursor-1",
                        "sender_id": "alice",
                        "sender_name": "Alice",
                        "timestamp": started_at.isoformat(),
                        "text": "发布前先检查导入 dry-run。",
                        "attachments": [
                            {
                                "id": "attachment-1",
                                "path": "release-notes.txt",
                                "filename": "release-notes.txt",
                                "mime": "text/plain",
                            }
                        ],
                    },
                    {
                        "id": "wx-2",
                        "cursor": "cursor-2",
                        "sender_id": "bob",
                        "sender_name": "Bob",
                        "timestamp": (started_at + timedelta(minutes=1)).isoformat(),
                        "text": "收到，准备执行正式导入。",
                    },
                ],
            }
        ],
    }
    if include_followup:
        payload["conversations"][0]["messages"].append(
            {
                "id": "wx-3",
                "cursor": "cursor-3",
                "sender_id": "alice",
                "sender_name": "Alice",
                "timestamp": (started_at + timedelta(minutes=2)).isoformat(),
                "text": "补一条 follow-up，验证 resume 只吃新消息。",
            }
        )
    media_root.mkdir(parents=True, exist_ok=True)
    (media_root / "release-notes.txt").write_text("release notes", encoding="utf-8")
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def _write_wechat_payload(path: Path, media_root: Path, conversations: list[dict]) -> None:
    media_root.mkdir(parents=True, exist_ok=True)
    (media_root / "release-notes.txt").write_text("release notes", encoding="utf-8")
    payload = {
        "account": {"label": "Connor"},
        "conversations": conversations,
    }
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def _write_weflow_jsonl_export(path: Path) -> None:
    rows = [
        {
            "_type": "header",
            "chatlab": {"version": "0.0.2", "generator": "WeFlow"},
            "meta": {
                "name": "发布群",
                "platform": "wechat",
                "type": "private",
            },
        },
        {
            "_type": "member",
            "platformId": "alice",
            "accountName": "Alice",
        },
        {
            "_type": "member",
            "platformId": "connor",
            "accountName": "Connor",
        },
        {
            "_type": "message",
            "sender": "alice",
            "accountName": "Alice",
            "timestamp": 1700000000,
            "type": 0,
            "content": "发布前先检查迁移 smoke。",
        },
        {
            "_type": "message",
            "sender": "connor",
            "accountName": "Connor",
            "timestamp": 1700000060,
            "type": 1,
            "content": "[图片]",
        },
    ]
    path.write_text(
        "\n".join(json.dumps(row, ensure_ascii=False) for row in rows),
        encoding="utf-8",
    )


@pytest.mark.asyncio
async def test_import_workbench_detect_preview_run_and_resume(tmp_path: Path) -> None:
    export_path = tmp_path / "wechat-export.json"
    media_root = tmp_path / "media"
    _write_wechat_export(export_path, media_root)

    service = ImportWorkbenchService(tmp_path, surface="cli")

    source = await service.detect_source(
        source_type="wechat",
        input_path=str(export_path),
        media_root=str(media_root),
        format_hint="json",
    )

    assert source.source_type == "wechat"
    assert source.detected_conversations[0].conversation_key == "release-room"
    assert source.detected_conversations[0].attachment_count == 1

    mapping = await service.save_mapping(source_id=source.source_id)

    assert mapping.conversation_mappings[0].scope_id.startswith("chat:wechat_import:")

    preview = await service.preview(
        source_id=source.source_id,
        mapping_id=mapping.mapping_id,
    )

    assert preview.status == ImportRunStatus.READY_TO_RUN
    assert preview.dry_run is True
    assert preview.summary["imported_count"] == 2
    assert preview.summary["attachment_count"] == 1

    run = await service.run(source_id=source.source_id, mapping_id=mapping.mapping_id)

    assert run.status == ImportRunStatus.COMPLETED
    assert run.summary["imported_count"] == 2
    assert run.summary["attachment_count"] == 1
    assert run.summary["attachment_artifact_count"] == 1
    assert run.summary["attachment_fragment_count"] == 1
    assert run.memory_effects.fragment_count == 2
    assert len(run.report_refs) == 1
    assert run.resume_ref == f"resume:{source.source_id}"

    workbench = await service.get_workbench()
    assert workbench.summary.source_count == 1
    assert workbench.summary.recent_run_count >= 2
    assert any(item.source_id == source.source_id for item in workbench.sources)
    assert any(item.resource_id == run.resource_id for item in workbench.recent_runs)

    stored_run = service.inspect_report(run.resource_id)
    assert stored_run.resource_id == run.resource_id

    store_group = await create_store_group(
        str(tmp_path / "data" / "sqlite" / "octoagent.db"),
        tmp_path / "data" / "artifacts",
    )
    try:
        project = await store_group.project_store.get_default_project()
        assert project is not None
        bindings = await store_group.project_store.list_bindings(project.project_id)
        assert any(
            item.binding_type == ProjectBindingType.IMPORT_SCOPE
            and item.binding_key == mapping.conversation_mappings[0].scope_id
            for item in bindings
        )
        artifacts = await store_group.artifact_store.list_artifacts_for_task("ops-chat-import")
        assert len(artifacts) == 2
    finally:
        await store_group.conn.close()

    _write_wechat_export(export_path, media_root, include_followup=True)
    source = await service.detect_source(
        source_type="wechat",
        input_path=str(export_path),
        media_root=str(media_root),
        format_hint="json",
    )

    resumed = await service.run(
        source_id=source.source_id,
        mapping_id=mapping.mapping_id,
        resume=True,
    )

    assert resumed.status == ImportRunStatus.COMPLETED
    assert resumed.summary["imported_count"] == 1
    assert resumed.summary["duplicate_count"] == 0


@pytest.mark.asyncio
async def test_import_workbench_sorts_messages_when_multiple_conversations_share_scope(
    tmp_path: Path,
) -> None:
    export_path = tmp_path / "merged-scope.json"
    media_root = tmp_path / "media"
    started_at = datetime.now(tz=UTC)
    _write_wechat_payload(
        export_path,
        media_root,
        [
            {
                "conversation_key": "room-newer",
                "label": "Room Newer",
                "messages": [
                    {
                        "id": "wx-new",
                        "cursor": "cursor-200",
                        "sender_id": "alice",
                        "sender_name": "Alice",
                        "timestamp": (started_at + timedelta(minutes=5)).isoformat(),
                        "text": "newer message should win cursor",
                    }
                ],
            },
            {
                "conversation_key": "room-older",
                "label": "Room Older",
                "messages": [
                    {
                        "id": "wx-old",
                        "cursor": "cursor-050",
                        "sender_id": "bob",
                        "sender_name": "Bob",
                        "timestamp": started_at.isoformat(),
                        "text": "older message should sort first",
                    }
                ],
            },
        ],
    )

    service = ImportWorkbenchService(tmp_path, surface="cli")
    source = await service.detect_source(
        source_type="wechat",
        input_path=str(export_path),
        media_root=str(media_root),
        format_hint="json",
    )

    scope_id = "chat:wechat_import:merged-scope"
    mapping = await service.save_mapping(
        source_id=source.source_id,
        conversation_mappings=[
            {
                "conversation_key": "room-newer",
                "conversation_label": "Room Newer",
                "scope_id": scope_id,
            },
            {
                "conversation_key": "room-older",
                "conversation_label": "Room Older",
                "scope_id": scope_id,
            },
        ],
    )

    preview = await service.preview(source_id=source.source_id, mapping_id=mapping.mapping_id)

    cursor_payload = preview.cursor["scopes"][scope_id]
    assert cursor_payload["cursor_value"] == "cursor-200"
    assert preview.summary["imported_count"] == 2


@pytest.mark.asyncio
async def test_import_workbench_run_failure_persists_failed_status(tmp_path: Path) -> None:
    export_path = tmp_path / "broken-export.json"
    media_root = tmp_path / "media"
    _write_wechat_export(export_path, media_root)

    service = ImportWorkbenchService(tmp_path, surface="cli")
    source = await service.detect_source(
        source_type="wechat",
        input_path=str(export_path),
        media_root=str(media_root),
        format_hint="json",
    )
    mapping = await service.save_mapping(source_id=source.source_id)
    export_path.unlink()

    with pytest.raises(ImportWorkbenchError, match="导入执行失败"):
        await service.run(source_id=source.source_id, mapping_id=mapping.mapping_id)

    workbench = await service.get_workbench()
    failed_run = next(item for item in workbench.recent_runs if item.source_id == source.source_id)
    assert failed_run.status == ImportRunStatus.FAILED
    assert failed_run.errors
    assert all(item.status != ImportRunStatus.RUNNING for item in workbench.recent_runs)


@pytest.mark.asyncio
async def test_import_workbench_rejects_mapping_from_other_source(tmp_path: Path) -> None:
    export_a = tmp_path / "wechat-a.json"
    export_b = tmp_path / "wechat-b.json"
    media_root = tmp_path / "media"
    _write_wechat_export(export_a, media_root, conversation_key="shared-room", label="Shared A")
    _write_wechat_export(export_b, media_root, conversation_key="shared-room", label="Shared B")

    service = ImportWorkbenchService(tmp_path, surface="cli")
    source_a = await service.detect_source(
        source_type="wechat",
        input_path=str(export_a),
        media_root=str(media_root),
        format_hint="json",
    )
    mapping_a = await service.save_mapping(source_id=source_a.source_id)

    source_b = await service.detect_source(
        source_type="wechat",
        input_path=str(export_b),
        media_root=str(media_root),
        format_hint="json",
    )

    with pytest.raises(ImportWorkbenchError, match="IMPORT_MAPPING_MISMATCH|mapping 不属于当前"):
        await service.preview(source_id=source_b.source_id, mapping_id=mapping_a.mapping_id)


@pytest.mark.asyncio
async def test_import_workbench_detects_weflow_jsonl_export(tmp_path: Path) -> None:
    export_path = tmp_path / "wechat-export.jsonl"
    _write_weflow_jsonl_export(export_path)

    service = ImportWorkbenchService(tmp_path, surface="cli")

    source = await service.detect_source(
        source_type="wechat",
        input_path=str(export_path),
        format_hint="jsonl",
    )

    assert source.source_type == "wechat"
    assert source.metadata["format"] == "jsonl"
    assert source.detected_conversations[0].label == "发布群"
    assert source.detected_conversations[0].message_count == 2

    mapping = await service.save_mapping(source_id=source.source_id)
    preview = await service.preview(source_id=source.source_id, mapping_id=mapping.mapping_id)

    assert preview.status == ImportRunStatus.READY_TO_RUN
    assert preview.summary["imported_count"] == 2
