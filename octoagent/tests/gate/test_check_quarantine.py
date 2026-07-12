"""F141 AC-2：quarantine manifest 校验器单测（repo-scripts/check-quarantine.py）。"""

from __future__ import annotations

import datetime as dt
import json
from pathlib import Path

import pytest

VALID_ENTRY = {
    "id": "gw:test-sample",
    "path": "apps/gateway/tests/test_sample.py::test_flaky_case",
    "reason": "CI 2-core 慢 runner 上 sleep 窗口间歇超时（junit rerun 计数 3 次/周）",
    "owner": "connor",
    "review_after": "2099-01-01",
    "exit_criteria": "改条件轮询后连续 2 周 CI 无 rerun 记录则删条目",
}


def write_manifest(tmp_path: Path, entries: list[dict]) -> Path:
    p = tmp_path / "quarantine.json"
    p.write_text(json.dumps({"quarantined": entries}), encoding="utf-8")
    return p


class TestSchemaValidation:
    def test_valid_manifest_loads(self, quarantine_mod, tmp_path: Path) -> None:
        p = write_manifest(tmp_path, [VALID_ENTRY])
        manifest = quarantine_mod.load_manifest(p)
        assert len(manifest["quarantined"]) == 1

    def test_empty_manifest_valid(self, quarantine_mod, tmp_path: Path) -> None:
        p = write_manifest(tmp_path, [])
        assert quarantine_mod.load_manifest(p)["quarantined"] == []

    @pytest.mark.parametrize("missing_field", [
        "id", "path", "reason", "owner", "review_after", "exit_criteria",
    ])
    def test_missing_field_fails(self, quarantine_mod, tmp_path: Path, missing_field: str) -> None:
        entry = {k: v for k, v in VALID_ENTRY.items() if k != missing_field}
        p = write_manifest(tmp_path, [entry])
        with pytest.raises(quarantine_mod.QuarantineError, match=missing_field):
            quarantine_mod.load_manifest(p)

    def test_empty_string_field_fails(self, quarantine_mod, tmp_path: Path) -> None:
        entry = {**VALID_ENTRY, "reason": "   "}
        p = write_manifest(tmp_path, [entry])
        with pytest.raises(quarantine_mod.QuarantineError, match="reason"):
            quarantine_mod.load_manifest(p)

    def test_duplicate_id_fails(self, quarantine_mod, tmp_path: Path) -> None:
        e2 = {**VALID_ENTRY, "path": "apps/gateway/tests/test_other.py"}
        p = write_manifest(tmp_path, [VALID_ENTRY, e2])
        with pytest.raises(quarantine_mod.QuarantineError, match="重复 id"):
            quarantine_mod.load_manifest(p)

    def test_duplicate_path_fails(self, quarantine_mod, tmp_path: Path) -> None:
        e2 = {**VALID_ENTRY, "id": "gw:another-id"}
        p = write_manifest(tmp_path, [VALID_ENTRY, e2])
        with pytest.raises(quarantine_mod.QuarantineError, match="重复 path"):
            quarantine_mod.load_manifest(p)

    @pytest.mark.parametrize("bad_date", ["2026-13-40", "not-a-date", "2026/07/01", ""])
    def test_bad_date_fails(self, quarantine_mod, tmp_path: Path, bad_date: str) -> None:
        entry = {**VALID_ENTRY, "review_after": bad_date}
        p = write_manifest(tmp_path, [entry])
        with pytest.raises(quarantine_mod.QuarantineError):
            quarantine_mod.load_manifest(p)

    def test_missing_file_fails(self, quarantine_mod, tmp_path: Path) -> None:
        with pytest.raises(quarantine_mod.QuarantineError, match="不存在"):
            quarantine_mod.load_manifest(tmp_path / "nope.json")

    def test_malformed_json_fails(self, quarantine_mod, tmp_path: Path) -> None:
        p = tmp_path / "quarantine.json"
        p.write_text("{broken", encoding="utf-8")
        with pytest.raises(quarantine_mod.QuarantineError, match="JSON"):
            quarantine_mod.load_manifest(p)

    def test_wrong_toplevel_shape_fails(self, quarantine_mod, tmp_path: Path) -> None:
        p = tmp_path / "quarantine.json"
        p.write_text(json.dumps([VALID_ENTRY]), encoding="utf-8")
        with pytest.raises(quarantine_mod.QuarantineError, match="顶层"):
            quarantine_mod.load_manifest(p)


class TestExpiryEnforcement:
    """AC-2②：过期即门禁 FAIL。"""

    def test_expired_entry_detected(self, quarantine_mod, tmp_path: Path) -> None:
        entry = {**VALID_ENTRY, "review_after": "2026-01-01"}
        p = write_manifest(tmp_path, [entry])
        manifest = quarantine_mod.load_manifest(p)
        expired = quarantine_mod.expired_entries(manifest, dt.date(2026, 7, 13))
        assert [e["id"] for e in expired] == [VALID_ENTRY["id"]]

    def test_review_day_itself_not_expired(self, quarantine_mod, tmp_path: Path) -> None:
        entry = {**VALID_ENTRY, "review_after": "2026-07-13"}
        p = write_manifest(tmp_path, [entry])
        manifest = quarantine_mod.load_manifest(p)
        assert quarantine_mod.expired_entries(manifest, dt.date(2026, 7, 13)) == []

    def test_cli_enforce_review_date_exit_1(self, quarantine_mod, tmp_path: Path) -> None:
        entry = {**VALID_ENTRY, "review_after": "2026-01-01"}
        p = write_manifest(tmp_path, [entry])
        rc = quarantine_mod.main([
            "--manifest", str(p), "--enforce-review-date", "--as-of", "2026-07-13",
        ])
        assert rc == 1

    def test_cli_not_expired_exit_0(self, quarantine_mod, tmp_path: Path) -> None:
        p = write_manifest(tmp_path, [VALID_ENTRY])
        rc = quarantine_mod.main([
            "--manifest", str(p), "--enforce-review-date", "--as-of", "2026-07-13",
        ])
        assert rc == 0

    def test_cli_expired_without_enforce_exit_0(self, quarantine_mod, tmp_path: Path) -> None:
        """非 gate 模式过期只 WARN 不 FAIL（本地 schema 校验语境）。"""
        entry = {**VALID_ENTRY, "review_after": "2026-01-01"}
        p = write_manifest(tmp_path, [entry])
        rc = quarantine_mod.main(["--manifest", str(p), "--as-of", "2026-07-13"])
        assert rc == 0

    def test_cli_schema_failure_exit_1(self, quarantine_mod, tmp_path: Path) -> None:
        p = write_manifest(tmp_path, [{**VALID_ENTRY, "owner": ""}])
        rc = quarantine_mod.main(["--manifest", str(p)])
        assert rc == 1

    def test_cli_bad_as_of_exit_2(self, quarantine_mod, tmp_path: Path) -> None:
        p = write_manifest(tmp_path, [])
        rc = quarantine_mod.main(["--manifest", str(p), "--as-of", "bogus"])
        assert rc == 2


class TestRepoManifest:
    """仓库真实 manifest 恒可通过 gate 校验（否则任何 pytest 会话都会炸）。"""

    def test_committed_manifest_valid_and_not_expired(self, quarantine_mod) -> None:
        rc = quarantine_mod.main(["--enforce-review-date"])
        assert rc == 0
