"""F151 Control Plane setup projection retirement contracts."""

from pathlib import Path

import pytest
from octoagent.core.models import SetupQuickConnectResult

SETUP_PROJECTION_ORACLE = "F151_SETUP_PROJECTION_RETIRED_FIELDS_PRESENT"
RETIRED_SETUP_FIELDS = {
    "activation",
    "activation_succeeded",
    "compose_file",
    "litellm_env_names",
    "litellm_sync_ok",
    "proxy_url",
    "runtime_activated",
}


def test_setup_projection_uses_provider_env_names_and_omits_retired_activation_fields() -> None:
    repo_root = Path(__file__).parents[6]
    paths = (
        repo_root
        / "octoagent/apps/gateway/src/octoagent/gateway/services/control_plane/mcp_service.py",
        repo_root
        / "octoagent/apps/gateway/src/octoagent/gateway/services/control_plane/setup_config_io.py",
        repo_root
        / "octoagent/apps/gateway/src/octoagent/gateway/services/control_plane/setup_service.py",
    )
    projection_source = "\n".join(path.read_text(encoding="utf-8") for path in paths)
    model_fields = set(SetupQuickConnectResult.model_fields)

    if (
        "provider_env_names" not in projection_source
        or RETIRED_SETUP_FIELDS & model_fields
        or any(field in projection_source for field in RETIRED_SETUP_FIELDS - {"activation"})
    ):
        pytest.fail(SETUP_PROJECTION_ORACLE, pytrace=False)
