"""CLI command dispatch in-process (the e2e suite covers real subprocesses)."""

import json
from pathlib import Path

import pytest
from helpers import update_settings

from photoproof.cli import main


def _run(root: Path, capsys: pytest.CaptureFixture[str], *argv: str) -> tuple[int, str, str]:
    code = main(list(argv), root=root)
    out, err = capsys.readouterr()
    return code, out, err


def test_commands(app_root: Path, target_file: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert _run(app_root, capsys, "version")[1].strip() == "0.1.0"
    code, out, _ = _run(app_root, capsys, "doctor")
    assert code == 6 and "runtime.directories" in out  # before first preflight
    code, out, _ = _run(app_root, capsys, "repair", "--dry-run")
    assert code == 0 and "[planned]" in out
    code, out, _ = _run(app_root, capsys, "repair")
    assert code == 0 and "[applied]" in out
    assert _run(app_root, capsys, "doctor")[0] == 0
    assert "No plugins installed" in _run(app_root, capsys, "plugins")[1]
    assert json.loads(_run(app_root, capsys, "plugins", "--json")[1]) == {
        "plugins": [],
        "unavailable": [],
    }
    assert "settings" in json.loads(_run(app_root, capsys, "config", "--json")[1])
    code, out, _ = _run(app_root, capsys, "status")
    assert code == 0 and "No production forensic detectors" in out
    code, out, _ = _run(app_root, capsys, "analyze", str(target_file), "--progress")
    assert code == 0 and "NO_DETECTORS" in out
    run_id = out.split()[1].rstrip(":")
    code, out, _ = _run(app_root, capsys, "manifest", run_id)
    assert code == 0 and run_id in out
    assert json.loads(_run(app_root, capsys, "manifest", run_id, "--json")[1])["run_id"] == run_id
    assert _run(app_root, capsys, "preflight")[1].strip() == "preflight passed"


def test_reference_requires_comparison_profile(
    app_root: Path, target_file: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    code, _, err = _run(
        app_root, capsys, "analyze", str(target_file), "--reference", str(target_file)
    )
    assert code == 1 and "does not accept a reference" in err


def test_unauthorized_bypass_reported(app_root: Path, capsys: pytest.CaptureFixture[str]) -> None:
    update_settings(app_root, security={"allow_blacklist_bypass": False})
    code, _, err = _run(app_root, capsys, "--ignore-blacklist", "plugins")
    assert code == 1 and "disabled" in err
