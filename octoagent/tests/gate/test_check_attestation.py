"""F141：attestation 清单校验器单测（repo-scripts/check-attestation.py）。

含对仓库真实 attestation-checklist.md 的解析回归（F144 yaml block 契约钉住）。
"""

from __future__ import annotations

import textwrap
from pathlib import Path

import pytest

pytest.importorskip("yaml", reason="PyYAML 是 provider/skills 传导依赖，venv 内恒有")


def write_checklist(tmp_path: Path, yaml_body: str) -> Path:
    p = tmp_path / "attestation-checklist.md"
    p.write_text(
        "# 标题\n\n前置说明\n\n```yaml\n" + textwrap.dedent(yaml_body) + "```\n\n尾注\n",
        encoding="utf-8",
    )
    return p


BASE_ITEM = """\
attestations:
  - id: ATT-129-BOOT
    source_ac: "F129 AC-1"
    why_physical: >-
      需要真实重启整台 Mac。
    action: "重启 Mac 验证"
    frequency: release
    last_attested: {last_attested}
    optional: false
"""


class TestParseValidation:
    def test_valid_checklist_loads(self, attestation_mod, tmp_path: Path) -> None:
        p = write_checklist(tmp_path, BASE_ITEM.format(last_attested="null"))
        items = attestation_mod.load_checklist(p)
        assert items[0]["id"] == "ATT-129-BOOT"
        assert items[0]["last_attested"] is None

    def test_no_yaml_block_fails(self, attestation_mod, tmp_path: Path) -> None:
        p = tmp_path / "attestation-checklist.md"
        p.write_text("# 无 yaml block\n", encoding="utf-8")
        with pytest.raises(attestation_mod.AttestationError, match="yaml"):
            attestation_mod.load_checklist(p)

    def test_missing_field_fails(self, attestation_mod, tmp_path: Path) -> None:
        body = """\
        attestations:
          - id: ATT-X
            source_ac: "x"
            action: "y"
            frequency: release
            last_attested: null
            optional: false
        """
        p = write_checklist(tmp_path, body)
        with pytest.raises(attestation_mod.AttestationError, match="why_physical"):
            attestation_mod.load_checklist(p)

    def test_duplicate_id_fails(self, attestation_mod, tmp_path: Path) -> None:
        body = BASE_ITEM.format(last_attested="null") + (
            '  - id: ATT-129-BOOT\n'
            '    source_ac: "dup"\n'
            '    why_physical: "dup"\n'
            '    action: "dup"\n'
            '    frequency: release\n'
            '    last_attested: null\n'
            '    optional: true\n'
        )
        p = write_checklist(tmp_path, body)
        with pytest.raises(attestation_mod.AttestationError, match="重复 id"):
            attestation_mod.load_checklist(p)

    def test_bad_last_attested_fails(self, attestation_mod, tmp_path: Path) -> None:
        p = write_checklist(tmp_path, BASE_ITEM.format(last_attested='"not-a-date"'))
        with pytest.raises(attestation_mod.AttestationError, match="last_attested"):
            attestation_mod.load_checklist(p)

    def test_non_bool_optional_fails(self, attestation_mod, tmp_path: Path) -> None:
        body = BASE_ITEM.format(last_attested="null").replace(
            "optional: false", 'optional: "false"'
        )
        p = write_checklist(tmp_path, body)
        with pytest.raises(attestation_mod.AttestationError, match="optional"):
            attestation_mod.load_checklist(p)

    def test_takes_first_yaml_block_only(self, attestation_mod, tmp_path: Path) -> None:
        """F144 契约：解析器取第一个 ```yaml block。"""
        p = tmp_path / "attestation-checklist.md"
        p.write_text(
            "```yaml\n" + textwrap.dedent(BASE_ITEM.format(last_attested="null"))
            + "```\n\n```yaml\nattestations: []\n```\n",
            encoding="utf-8",
        )
        items = attestation_mod.load_checklist(p)
        assert len(items) == 1


class TestRequireSigned:
    """release gate 模式：--require-signed。"""

    def test_null_last_attested_unsigned(self, attestation_mod, tmp_path: Path) -> None:
        p = write_checklist(tmp_path, BASE_ITEM.format(last_attested="null"))
        rc = attestation_mod.main([
            "--checklist", str(p), "--require-signed", "--as-of", "2026-07-13",
        ])
        assert rc == 1

    def test_fresh_signature_passes(self, attestation_mod, tmp_path: Path) -> None:
        p = write_checklist(tmp_path, BASE_ITEM.format(last_attested="2026-07-01"))
        rc = attestation_mod.main([
            "--checklist", str(p), "--require-signed", "--as-of", "2026-07-13",
        ])
        assert rc == 0

    def test_stale_signature_fails(self, attestation_mod, tmp_path: Path) -> None:
        p = write_checklist(tmp_path, BASE_ITEM.format(last_attested="2026-01-01"))
        rc = attestation_mod.main([
            "--checklist", str(p), "--require-signed",
            "--as-of", "2026-07-13", "--attest-max-age", "90",
        ])
        assert rc == 1

    def test_max_age_boundary_inclusive(self, attestation_mod, tmp_path: Path) -> None:
        """恰好 90 天 = 有效（> max_age 才过期）。"""
        p = write_checklist(tmp_path, BASE_ITEM.format(last_attested="2026-04-14"))
        rc = attestation_mod.main([
            "--checklist", str(p), "--require-signed",
            "--as-of", "2026-07-13", "--attest-max-age", "90",
        ])
        assert rc == 0

    def test_optional_item_never_blocks(self, attestation_mod, tmp_path: Path) -> None:
        body = BASE_ITEM.format(last_attested="2026-07-01") + (
            '  - id: ATT-OPTIONAL-UX\n'
            '    source_ac: "移动端体验抽查"\n'
            '    why_physical: "真实移动设备人眼体验"\n'
            '    action: "手机浏览器抽查"\n'
            '    frequency: release\n'
            '    last_attested: null\n'
            '    optional: true\n'
        )
        p = write_checklist(tmp_path, body)
        rc = attestation_mod.main([
            "--checklist", str(p), "--require-signed", "--as-of", "2026-07-13",
        ])
        assert rc == 0

    def test_parse_only_mode_ignores_signing(self, attestation_mod, tmp_path: Path) -> None:
        p = write_checklist(tmp_path, BASE_ITEM.format(last_attested="null"))
        rc = attestation_mod.main(["--checklist", str(p)])
        assert rc == 0


class TestRepoChecklist:
    """仓库真实清单恒可解析（F144 契约回归；签署状态不在此断言——那是 release gate 的事）。"""

    def test_committed_checklist_parses(self, attestation_mod) -> None:
        rc = attestation_mod.main([])
        assert rc == 0

    def test_committed_checklist_has_att_129_boot(self, attestation_mod) -> None:
        items = attestation_mod.load_checklist(attestation_mod.DEFAULT_CHECKLIST)
        ids = {i["id"] for i in items}
        assert "ATT-129-BOOT" in ids
