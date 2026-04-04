"""PathAccessPolicy 单元测试 — 白名单/黑名单/灰名单路径访问判定"""

from pathlib import Path

import pytest

from octoagent.tooling.path_policy import PathVerdict, check_path_access

INSTANCE_ROOT = Path("/home/user/.octoagent")
PROJECT_SLUG = "default"


class TestWhitelist:
    """白名单路径应自动放行"""

    def test_current_project_workspace(self):
        path = INSTANCE_ROOT / "projects" / "default" / "workspace" / "notes.txt"
        result = check_path_access(path, INSTANCE_ROOT, PROJECT_SLUG)
        assert result.verdict == PathVerdict.ALLOW
        assert "current_project" in result.reason

    def test_current_project_behavior(self):
        path = INSTANCE_ROOT / "projects" / "default" / "behavior" / "PROJECT.md"
        result = check_path_access(path, INSTANCE_ROOT, PROJECT_SLUG)
        assert result.verdict == PathVerdict.ALLOW

    def test_global_behavior(self):
        path = INSTANCE_ROOT / "behavior" / "system" / "AGENTS.md"
        result = check_path_access(path, INSTANCE_ROOT, PROJECT_SLUG)
        assert result.verdict == PathVerdict.ALLOW
        assert "whitelist_dir:behavior" in result.reason

    def test_skills_dir(self):
        path = INSTANCE_ROOT / "skills" / "my_skill" / "SKILL.md"
        result = check_path_access(path, INSTANCE_ROOT, PROJECT_SLUG)
        assert result.verdict == PathVerdict.ALLOW
        assert "whitelist_dir:skills" in result.reason

    def test_mcp_servers_dir(self):
        path = INSTANCE_ROOT / "mcp-servers" / "config.json"
        result = check_path_access(path, INSTANCE_ROOT, PROJECT_SLUG)
        assert result.verdict == PathVerdict.ALLOW


class TestBlacklist:
    """黑名单路径应直接拒绝"""

    def test_app_source_code(self):
        path = INSTANCE_ROOT / "app" / "octoagent" / "apps" / "gateway" / "main.py"
        result = check_path_access(path, INSTANCE_ROOT, PROJECT_SLUG)
        assert result.verdict == PathVerdict.DENY
        assert "blacklist_dir:app" in result.reason

    def test_app_root(self):
        path = INSTANCE_ROOT / "app"
        result = check_path_access(path, INSTANCE_ROOT, PROJECT_SLUG)
        assert result.verdict == PathVerdict.DENY

    def test_data_dir(self):
        path = INSTANCE_ROOT / "data" / "sqlite" / "main.db"
        result = check_path_access(path, INSTANCE_ROOT, PROJECT_SLUG)
        assert result.verdict == PathVerdict.DENY
        assert "blacklist_dir:data" in result.reason

    def test_bin_dir(self):
        path = INSTANCE_ROOT / "bin" / "octo-start"
        result = check_path_access(path, INSTANCE_ROOT, PROJECT_SLUG)
        assert result.verdict == PathVerdict.DENY

    def test_env_file(self):
        path = INSTANCE_ROOT / ".env.litellm"
        result = check_path_access(path, INSTANCE_ROOT, PROJECT_SLUG)
        assert result.verdict == PathVerdict.DENY
        assert "blacklist_prefix:.env" in result.reason

    def test_env_plain(self):
        path = INSTANCE_ROOT / ".env"
        result = check_path_access(path, INSTANCE_ROOT, PROJECT_SLUG)
        assert result.verdict == PathVerdict.DENY

    def test_auth_profiles(self):
        path = INSTANCE_ROOT / "auth-profiles.json"
        result = check_path_access(path, INSTANCE_ROOT, PROJECT_SLUG)
        assert result.verdict == PathVerdict.DENY
        assert "blacklist_file" in result.reason

    def test_octoagent_yaml(self):
        path = INSTANCE_ROOT / "octoagent.yaml"
        result = check_path_access(path, INSTANCE_ROOT, PROJECT_SLUG)
        assert result.verdict == PathVerdict.DENY

    def test_litellm_config(self):
        path = INSTANCE_ROOT / "litellm-config.yaml"
        result = check_path_access(path, INSTANCE_ROOT, PROJECT_SLUG)
        assert result.verdict == PathVerdict.DENY

    def test_cross_project(self):
        path = INSTANCE_ROOT / "projects" / "other_project" / "workspace" / "secret.txt"
        result = check_path_access(path, INSTANCE_ROOT, PROJECT_SLUG)
        assert result.verdict == PathVerdict.DENY
        assert "cross_project" in result.reason

    def test_projects_root_listing(self):
        path = INSTANCE_ROOT / "projects"
        result = check_path_access(path, INSTANCE_ROOT, PROJECT_SLUG)
        assert result.verdict == PathVerdict.DENY
        assert "projects_root_listing" in result.reason

    def test_instance_root_direct(self):
        result = check_path_access(INSTANCE_ROOT, INSTANCE_ROOT, PROJECT_SLUG)
        assert result.verdict == PathVerdict.DENY
        assert "instance_root_direct" in result.reason

    def test_unknown_toplevel_dir(self):
        path = INSTANCE_ROOT / "some_unknown_dir" / "file.txt"
        result = check_path_access(path, INSTANCE_ROOT, PROJECT_SLUG)
        assert result.verdict == PathVerdict.DENY
        assert "unknown_toplevel" in result.reason


class TestGreylist:
    """灰名单路径（instance root 外）应走审批"""

    def test_home_ssh(self):
        path = Path("/home/user/.ssh/id_rsa")
        result = check_path_access(path, INSTANCE_ROOT, PROJECT_SLUG)
        assert result.verdict == PathVerdict.NEEDS_APPROVAL
        assert "outside_instance_root" in result.reason

    def test_absolute_path_outside(self):
        path = Path("/tmp/some_file.txt")
        result = check_path_access(path, INSTANCE_ROOT, PROJECT_SLUG)
        assert result.verdict == PathVerdict.NEEDS_APPROVAL

    def test_etc_passwd(self):
        path = Path("/etc/passwd")
        result = check_path_access(path, INSTANCE_ROOT, PROJECT_SLUG)
        assert result.verdict == PathVerdict.NEEDS_APPROVAL


class TestNoProject:
    """无 project slug 时的行为"""

    def test_project_dir_without_slug(self):
        """无 slug 时不存在 current project，所有 project 目录都被拒"""
        path = INSTANCE_ROOT / "projects" / "default" / "workspace" / "test.txt"
        result = check_path_access(path, INSTANCE_ROOT, "")
        assert result.verdict == PathVerdict.DENY
        assert "cross_project" in result.reason

    def test_behavior_still_allowed(self):
        """无 slug 时全局 behavior 仍可访问"""
        path = INSTANCE_ROOT / "behavior" / "system" / "USER.md"
        result = check_path_access(path, INSTANCE_ROOT, "")
        assert result.verdict == PathVerdict.ALLOW
