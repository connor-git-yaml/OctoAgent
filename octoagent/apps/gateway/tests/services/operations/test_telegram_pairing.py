from __future__ import annotations

import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest
from octoagent.gateway.services.operations.telegram_pairing import TelegramState, TelegramStateStore

_RMW_ORACLE = "F151_TELEGRAM_RMW_ATOMICITY_MISSING"


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
    assert store.resolve_reply_thread_root(chat_id="-1001", message_id="9001") == "88"


def test_state_store_recovers_from_corrupted_file(tmp_path: Path) -> None:
    store = TelegramStateStore(tmp_path)
    store.path.parent.mkdir(parents=True, exist_ok=True)
    store.path.write_text("{bad json", encoding="utf-8")

    state = store.load()
    assert state.approved_users == {}
    assert store.last_issue == "corrupted"
    assert store.path.with_suffix(".json.corrupted").exists()


def test_two_store_instances_preserve_delete_and_offset_under_barrier(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "project"
    seed = TelegramStateStore(root)
    seed.upsert_approved_user(user_id=42, chat_id=42, username="alice")
    seed.set_polling_offset(7)

    delete_store = TelegramStateStore(root)
    offset_store = TelegramStateStore(root)
    loaded = threading.Barrier(2)
    delete_completed = threading.Event()
    offset_completed = threading.Event()

    for store in (delete_store, offset_store):
        original_load = store.load

        def synchronized_load(
            load: object = original_load,
        ) -> TelegramState:
            assert callable(load)
            state = load()
            loaded.wait(timeout=5)
            return state

        monkeypatch.setattr(store, "load", synchronized_load)

    def delete_user() -> None:
        delete_store.delete_approved_user(42)
        delete_completed.set()

    def update_offset() -> None:
        offset_store.set_polling_offset(99)
        offset_completed.set()

    with ThreadPoolExecutor(max_workers=2) as executor:
        delete_future = executor.submit(delete_user)
        offset_future = executor.submit(update_offset)
        delete_future.result(timeout=5)
        offset_future.result(timeout=5)

    assert delete_completed.is_set()
    assert offset_completed.is_set()
    final_state = TelegramStateStore(root).load()
    if "42" in final_state.approved_users or final_state.polling_offset != 99:
        pytest.fail(_RMW_ORACLE, pytrace=False)

    before_failure = seed.path.read_bytes()
    real_replace = Path.replace

    def reject_replace(path: Path, target: Path) -> Path:
        if target == seed.path:
            raise OSError("injected atomic replace failure")
        return real_replace(path, target)

    monkeypatch.setattr(Path, "replace", reject_replace)
    with pytest.raises(OSError, match="injected atomic replace failure"):
        TelegramStateStore(root).set_polling_offset(100)
    assert seed.path.read_bytes() == before_failure
    assert list(seed.path.parent.glob("*.tmp")) == []
