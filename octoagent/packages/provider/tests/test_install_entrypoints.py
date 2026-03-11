from __future__ import annotations

import subprocess
from pathlib import Path


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[4]


def test_remote_user_installer_exists_and_shell_parses() -> None:
    script_path = _repo_root() / "repo-scripts" / "install-octo-user.sh"

    assert script_path.exists()
    result = subprocess.run(
        ["bash", "-n", str(script_path)],
        check=False,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr


def test_readme_references_existing_remote_installer() -> None:
    readme_path = _repo_root() / "octoagent" / "README.md"
    content = readme_path.read_text(encoding="utf-8")

    assert (
        "https://raw.githubusercontent.com/connor-git-yaml/OctoAgent/master/repo-scripts/install-octo-user.sh"
        in content
    )


def test_remote_user_installer_points_to_octo_setup() -> None:
    script_path = _repo_root() / "repo-scripts" / "install-octo-user.sh"
    content = script_path.read_text(encoding="utf-8")

    assert "octo setup" in content
    assert "octo config init" not in content
