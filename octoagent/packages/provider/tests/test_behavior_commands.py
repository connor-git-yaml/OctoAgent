from __future__ import annotations

from pathlib import Path

from click.testing import CliRunner
from octoagent.provider.dx.cli import main


def test_behavior_init_creates_project_files(tmp_path: Path) -> None:
    runner = CliRunner()
    env = {"OCTOAGENT_PROJECT_ROOT": str(tmp_path)}

    result = runner.invoke(main, ["behavior", "init"], env=env)

    assert result.exit_code == 0
    assert "Behavior Init" in result.output
    assert "scope=project" in result.output
    target_dir = tmp_path / "projects" / "default" / "behavior"
    project_root = tmp_path / "projects" / "default"
    assert (target_dir / "USER.md").exists()
    assert (target_dir / "PROJECT.md").exists()
    assert (target_dir / "KNOWLEDGE.md").exists()
    assert (target_dir / "TOOLS.md").exists()
    assert (target_dir / "instructions" / "README.md").exists()
    assert (project_root / "workspace").exists()
    assert (project_root / "data").exists()
    assert (project_root / "notes").exists()
    assert (project_root / "artifacts").exists()
    assert (project_root / "project.secret-bindings.json").exists()
    assert "created_dirs=" in result.output
    assert "extra_files=" in result.output
    assert "项目目标" in (target_dir / "PROJECT.md").read_text(encoding="utf-8")
    assert "Storage Boundaries" in (
        target_dir / "instructions" / "README.md"
    ).read_text(encoding="utf-8")
    assert '"bindings": []' in (
        project_root / "project.secret-bindings.json"
    ).read_text(encoding="utf-8")


def test_behavior_ls_and_show_report_effective_sources(tmp_path: Path) -> None:
    runner = CliRunner()
    env = {"OCTOAGENT_PROJECT_ROOT": str(tmp_path)}

    initialized = runner.invoke(main, ["behavior", "init", "--scope", "system"], env=env)
    assert initialized.exit_code == 0

    listed = runner.invoke(main, ["behavior", "ls"], env=env)
    assert listed.exit_code == 0
    assert "Behavior Workspace" in listed.output
    assert "filesystem:behavior/system" in listed.output
    assert "agent_slug=main" in listed.output
    assert "system_dir=behavior/system" in listed.output
    assert "workspace_root=" in listed.output
    # Rich 面板在窄终端下会把长路径硬换行，行首尾都带有 │ 边框字符。
    # 去掉每行首尾的边框字符（│、╭、╰ 等）和空白后无间隔拼接，
    # 使跨行截断的路径片段能被完整匹配。
    flat_output = "".join(
        line.strip().strip("│╭╰─").strip()
        for line in listed.output.splitlines()
    )
    assert "projects/default/workspace" in flat_output

    shown = runner.invoke(main, ["behavior", "show", "agents"], env=env)
    assert shown.exit_code == 0
    assert "Behavior File" in shown.output
    assert "file=AGENTS.md" in shown.output
    assert "source_kind=system_file" in shown.output
    assert "editable_mode=proposal_required" in shown.output
    assert "review_mode=review_required" in shown.output
    assert "Behavior File" in shown.output


def test_behavior_agent_scope_uses_requested_agent_slug(tmp_path: Path) -> None:
    runner = CliRunner()
    env = {"OCTOAGENT_PROJECT_ROOT": str(tmp_path)}

    init_result = runner.invoke(main, ["behavior", "init", "--scope", "agent", "--agent", "Research Root Agent"], env=env)
    assert init_result.exit_code == 0
    assert "agent_slug=research-root-agent" in init_result.output
    assert (tmp_path / "behavior" / "agents" / "research-root-agent" / "IDENTITY.md").exists()

    edit_result = runner.invoke(
        main,
        ["behavior", "edit", "TOOLS", "--scope", "project-agent", "--agent", "Research Root Agent", "--no-launch"],
        env=env,
    )
    assert edit_result.exit_code == 0
    assert "agent_slug=research-root-agent" in edit_result.output
    assert (
        tmp_path / "projects" / "default" / "behavior" / "agents" / "research-root-agent" / "TOOLS.md"
    ).exists()


def test_behavior_agent_scope_uses_distinct_hash_slug_for_non_ascii_agent_names(
    tmp_path: Path,
) -> None:
    runner = CliRunner()
    env = {"OCTOAGENT_PROJECT_ROOT": str(tmp_path)}

    result_research = runner.invoke(
        main,
        ["behavior", "init", "--scope", "agent", "--agent", "研究员"],
        env=env,
    )
    result_reviewer = runner.invoke(
        main,
        ["behavior", "init", "--scope", "agent", "--agent", "审稿助手"],
        env=env,
    )

    assert result_research.exit_code == 0
    assert result_reviewer.exit_code == 0
    assert "agent_slug=agent-" in result_research.output
    assert "agent_slug=agent-" in result_reviewer.output

    agent_dirs = sorted(
        path.name
        for path in (tmp_path / "behavior" / "agents").iterdir()
        if path.is_dir()
    )
    assert len(agent_dirs) == 2
    assert agent_dirs[0] != agent_dirs[1]


def test_behavior_edit_diff_and_apply_manage_project_override_files(tmp_path: Path) -> None:
    runner = CliRunner()
    env = {"OCTOAGENT_PROJECT_ROOT": str(tmp_path)}

    edit_result = runner.invoke(
        main,
        ["behavior", "edit", "PROJECT", "--no-launch"],
        env=env,
    )
    assert edit_result.exit_code == 0
    assert "Behavior Edit" in edit_result.output
    target_file = tmp_path / "projects" / "default" / "behavior" / "PROJECT.md"
    assert target_file.exists()

    target_file.write_text("第一行\\n第二行\\n", encoding="utf-8")
    diff_result = runner.invoke(main, ["behavior", "diff", "PROJECT"], env=env)
    assert diff_result.exit_code == 0
    assert "Behavior Diff" in diff_result.output
    assert "--- base:project_file" in diff_result.output
    assert "+++ candidate:projects/default/behavior/PROJECT.md" in diff_result.output

    proposal = tmp_path / "proposal-agents.md"
    proposal.write_text("新的 AGENTS 提案\n", encoding="utf-8")
    apply_result = runner.invoke(
        main,
        ["behavior", "apply", "PROJECT", "--from", str(proposal)],
        env=env,
    )
    assert apply_result.exit_code == 0
    assert "Behavior Apply" in apply_result.output
    assert target_file.read_text(encoding="utf-8") == "新的 AGENTS 提案\n"


def test_behavior_diff_project_agent_tools_uses_system_layer_as_base(tmp_path: Path) -> None:
    runner = CliRunner()
    env = {"OCTOAGENT_PROJECT_ROOT": str(tmp_path)}
    system_dir = tmp_path / "behavior" / "system"
    system_dir.mkdir(parents=True)
    (system_dir / "TOOLS.md").write_text("system tools content\n", encoding="utf-8")

    diff_result = runner.invoke(
        main,
        ["behavior", "diff", "TOOLS", "--scope", "project-agent"],
        env=env,
    )

    assert diff_result.exit_code == 0
    assert "effective_source=system_file" in diff_result.output
    assert "当前 override 与下层来源没有差异" in diff_result.output
