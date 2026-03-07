"""Feature 021 core 扩展测试。"""

from octoagent.core.models.enums import EventType
from octoagent.core.models.payloads import ChatImportLifecyclePayload


class TestChatImportEventTypes:
    def test_chat_import_started(self) -> None:
        assert EventType.CHAT_IMPORT_STARTED == "CHAT_IMPORT_STARTED"

    def test_chat_import_completed(self) -> None:
        assert EventType.CHAT_IMPORT_COMPLETED == "CHAT_IMPORT_COMPLETED"

    def test_chat_import_failed(self) -> None:
        assert EventType.CHAT_IMPORT_FAILED == "CHAT_IMPORT_FAILED"


class TestChatImportLifecyclePayload:
    def test_payload_defaults(self) -> None:
        payload = ChatImportLifecyclePayload(
            batch_id="batch-001",
            source_id="source-001",
            scope_id="chat:wechat_import:project-alpha",
        )
        assert payload.imported_count == 0
        assert payload.report_id is None
        assert payload.message == ""
