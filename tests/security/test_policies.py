from dataclasses import fields
from pathlib import Path

import pytest
from helpers import update_settings

from photoproof.cli import main as cli_main
from photoproof.contracts.exceptions import ConfigurationError, InputRejectedError
from photoproof.contracts.plugins import PluginContext
from photoproof.core.config.loader import load_identity, load_settings
from photoproof.core.security.inputs import open_regular_file


def test_non_regular_inputs_rejected(tmp_path: Path) -> None:
    with pytest.raises(InputRejectedError, match="not a regular file"):
        open_regular_file(tmp_path)


def test_configuration_cannot_add_plugin_locations(app_root: Path) -> None:
    update_settings(app_root, plugins={"package": "os", "directories": ["/srv/untrusted"]})
    with pytest.raises(ConfigurationError, match="Extra inputs are not permitted"):
        load_settings(app_root, load_identity(app_root), environ={})


def test_plugin_context_exposes_no_filesystem_paths() -> None:
    names = {f.name for f in fields(PluginContext)}
    assert names == {"run_id", "plugin_id", "inputs", "config", "seed", "write_artifact", "logger"}


@pytest.mark.parametrize("run_id", ["../../app", "..%2f..%2fsettings", "/etc/passwd"])
def test_manifest_lookup_cannot_escape(
    app_root: Path, run_id: str, capsys: pytest.CaptureFixture[str]
) -> None:
    code = cli_main(["manifest", run_id], root=app_root)
    assert code == 1
    err = capsys.readouterr().err
    assert "escapes its root" in err or "not found" in err


def test_production_forbids_blacklist_bypass(app_root: Path) -> None:
    update_settings(app_root, environment={"mode": "production"})
    with pytest.raises(ConfigurationError, match="allow_blacklist_bypass"):
        load_settings(app_root, load_identity(app_root), environ={})


def test_production_ui_must_bind_loopback(app_root: Path) -> None:
    update_settings(
        app_root,
        environment={"mode": "production"},
        security={"allow_blacklist_bypass": False},
        ui={"host": "0.0.0.0"},  # noqa: S104 - the point of the test
    )
    with pytest.raises(ConfigurationError, match="loopback"):
        load_settings(app_root, load_identity(app_root), environ={})
