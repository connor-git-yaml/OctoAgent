from click.testing import CliRunner
from octoagent.provider.dx.cli import main


def test_update_dry_run_command(monkeypatch) -> None:
    class FakeService:
        def __init__(self, _project_root) -> None:
            pass

        async def preview(self, *, trigger_source):
            assert trigger_source == "cli"

            class Summary:
                attempt_id = "attempt-001"
                overall_status = "SUCCEEDED"
                current_phase = "migrate"
                management_mode = "managed"
                phases = []
                failure_report = None

            return Summary()

    monkeypatch.setattr("octoagent.provider.dx.update_commands.UpdateService", FakeService)
    runner = CliRunner()

    result = runner.invoke(main, ["update", "--dry-run"])

    assert result.exit_code == 0
    assert "Update Dry Run" in result.output
    assert "attempt-001" in result.output


def test_restart_command_failure_returns_exit_1(monkeypatch) -> None:
    class FakeFailureReport:
        message = "restart failed"

    class FakeService:
        def __init__(self, _project_root) -> None:
            pass

        async def restart(self, *, trigger_source):
            assert trigger_source == "cli"

            class Summary:
                attempt_id = "attempt-002"
                overall_status = "FAILED"
                current_phase = "restart"
                management_mode = "managed"
                phases = []
                failure_report = FakeFailureReport()

            return Summary()

    monkeypatch.setattr("octoagent.provider.dx.update_commands.UpdateService", FakeService)
    runner = CliRunner()

    result = runner.invoke(main, ["restart"])

    assert result.exit_code == 1
    assert "restart failed" in result.output
