"""Real subprocess invocations of main.py and the CLI (against a temporary root)."""

import json
import subprocess
import sys
from pathlib import Path

from helpers import APP_VERSION, REPO


def _cli(root: Path, *args: str) -> subprocess.CompletedProcess[str]:
    code = (
        f"import sys; from pathlib import Path; from photoproof.cli import main; "
        f"sys.exit(main(sys.argv[1:], root=Path({str(root)!r})))"
    )
    return subprocess.run(
        [sys.executable, "-c", code, *args], capture_output=True, text=True, check=False, timeout=60
    )


def test_launcher_version() -> None:
    result = subprocess.run(
        [sys.executable, str(REPO / "main.py"), "--version"],
        capture_output=True,
        text=True,
        check=False,
        timeout=60,
    )
    assert result.returncode == 0 and result.stdout.strip() == f"PhotoProof {APP_VERSION}"


def test_cli_status_doctor_analyze_manifest(app_root: Path, target_file: Path) -> None:
    status = _cli(app_root, "status")
    assert status.returncode == 0, status.stderr
    assert "registered" in status.stdout

    doctor = _cli(app_root, "doctor", "--json")
    assert doctor.returncode == 0, doctor.stdout
    assert all(r["severity"] == "ok" for r in json.loads(doctor.stdout))

    analyze = _cli(app_root, "analyze", str(target_file), "--json")
    result = json.loads(analyze.stdout)
    # The fixture is an undecodable JPEG: the pixel detectors skip and the run reports
    # INCOMPLETE, which exits nonzero. It must never claim a verdict.
    assert result["status"] == "INCOMPLETE" and analyze.returncode == 7
    assert "verdict" not in result

    shown = _cli(app_root, "manifest", result["run_id"])
    assert shown.returncode == 0 and result["run_id"] in shown.stdout

    config = _cli(app_root, "config", "--json")
    assert json.loads(config.stdout)["feature_flags"]["cache.run_level"] is True


def test_cli_rejects_oversized_input(app_root: Path, tmp_path: Path) -> None:
    settings = app_root / "settings.yaml"
    settings.write_text(
        settings.read_text().replace("max_input_bytes: 104857600", "max_input_bytes: 4")
    )
    big = tmp_path / "big.bin"
    big.write_bytes(b"0123456789")
    result = _cli(app_root, "analyze", str(big))
    assert result.returncode == 1 and "max_input_bytes" in result.stderr


def test_cli_ignore_blacklist_flag_accepted(app_root: Path, target_file: Path) -> None:
    result = _cli(app_root, "--ignore-blacklist", "analyze", str(target_file))
    # The flag is accepted (no policy error); the run itself is INCOMPLETE (exit 7)
    # because the fixture JPEG cannot be decoded.
    assert result.returncode in {0, 7}, result.stderr
    assert "INCOMPLETE" in result.stdout or "COMPLETED" in result.stdout
