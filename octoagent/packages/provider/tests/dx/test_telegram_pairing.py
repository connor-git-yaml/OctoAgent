from __future__ import annotations

from pathlib import Path

from octoagent.provider.dx.telegram_pairing import TelegramState, TelegramStateStore


def test_state_store_reads_and_writes_core_fields(tmp_path: Path) -> None:
    store = TelegramStateStore(tmp_path)

    store.upsert_pending_pairing(
        user_id=123,
        chat_id=123,
        username="alice",
        last_message_text="/start",
    )
    store.upsert_approved_user(
        user_id=456,
        chat_id=456,
        username="bob",
        message_id=99,
    )
    store.set_allowed_groups([-1001, -1002])
    store.set_polling_offset(42)

    state = store.load()
    assert isinstance(state, TelegramState)
    assert state.pending_pairings["123"].username == "alice"
    assert state.approved_users["456"].last_message_id == 99
    assert state.allowed_groups == ["-1001", "-1002"]
    assert state.polling_offset == 42


def test_record_dm_message_promotes_known_user_and_creates_pending(tmp_path: Path) -> None:
    store = TelegramStateStore(tmp_path)
    store.upsert_approved_user(user_id=123, chat_id=123, username="alice")

    store.record_dm_message(
        user_id=123,
        chat_id=123,
        username="alice",
        message_id=8,
        text="hello",
    )
    store.record_dm_message(
        user_id=789,
        chat_id=789,
        username="charlie",
        text="/start",
    )

    approved = store.get_approved_user("123")
    pending = store.get_pending_pairing("789")
    assert approved is not None
    assert approved.last_message_id == 8
    assert pending is not None
    assert pending.last_message_text == "/start"


def test_reply_thread_root_roundtrip(tmp_path: Path) -> None:
    store = TelegramStateStore(tmp_path)

    root = store.remember_reply_thread_root(
        chat_id=-1001,
        message_id=9001,
        root_message_id=88,
    )

    assert root == "88"
    assert (
        store.resolve_reply_thread_root(chat_id="-1001", message_id="9001")
        == "88"
    )


def test_state_store_recovers_from_corrupted_file(tmp_path: Path) -> None:
    store = TelegramStateStore(tmp_path)
    store.path.parent.mkdir(parents=True, exist_ok=True)
    store.path.write_text("{bad json", encoding="utf-8")

    state = store.load()
    assert state.approved_users == {}
    assert store.last_issue == "corrupted"
    assert store.path.with_suffix(".json.corrupted").exists()
