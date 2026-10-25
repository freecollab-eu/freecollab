"""Execute the actual private-homeserver initializer against owned configurations."""
import os
from pathlib import Path
import subprocess
import sys

import pytest
import yaml


SCRIPT = Path(__file__).resolve().parents[2] / "scripts/secure-matrix-config.py"


def initialize(path, **overrides):
    environment = {key: value for key, value in os.environ.items()
                   if not key.startswith(("MATRIX_", "SYNAPSE_"))}
    environment.update(MATRIX_SERVER_NAME="matrix.config.example",
                       MATRIX_REGISTRATION_SHARED_SECRET="owned-config-fixture-secret",
                       MATRIX_FEDERATION_DOMAINS='["peer.config.example"]')
    environment.update(overrides)
    return subprocess.run([sys.executable, str(SCRIPT), str(path)], env=environment,
                          capture_output=True, text=True, timeout=10)


@pytest.mark.parametrize("existing", [None, {"per_second": 2, "burst_count": 37}])
def test_actual_initializer_supports_collaboration_without_replacing_operator_rate_policy(tmp_path, existing):
    path = tmp_path / "homeserver.yaml"
    original = {"server_name": "matrix.config.example", "enable_registration": True}
    if existing is not None:
        original["rc_message"] = existing
    path.write_text(yaml.safe_dump(original))
    result = initialize(path)
    assert result.returncode == 0, result.stderr
    config = yaml.safe_load(path.read_text())
    assert config["rc_message"] == (existing or {"per_second": 10, "burst_count": 100})
    assert config["enable_registration"] is False
    assert config["federation_domain_whitelist"] == ["peer.config.example"]
    assert config["allow_public_rooms_without_auth"] is False
    assert config["listeners"][0]["x_forwarded"] is False
    assert path.stat().st_mode & 0o777 == 0o600
    assert "owned-config-fixture-secret" not in result.stdout + result.stderr


def test_actual_initializer_refuses_homeserver_identity_change_before_rewriting(tmp_path):
    path = tmp_path / "homeserver.yaml"
    original = yaml.safe_dump({"server_name": "retained.config.example"})
    path.write_text(original)
    result = initialize(path)
    assert result.returncode != 0
    assert "Refusing to change the identity" in result.stderr
    assert path.read_text() == original
