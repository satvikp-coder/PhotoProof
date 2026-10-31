"""Stored reports verify source bytes and publish derived files without changing evidence."""

import json
import os
import subprocess
from collections.abc import Callable
from pathlib import Path

import pytest

from photoproof.cli import main as cli_main
from photoproof.contracts.analysis import AnalysisRequest
from photoproof.core.bootstrap import Application
from photoproof.core.controller import AnalysisController
from photoproof.core.reporting import ReportError, build_report, canonical_report_json
from photoproof.core.reporting.service import generate_report
from photoproof.core.storage.store import sha256_bytes

Factory = Callable[..., Application]


def stored_run(app: Application, source: Path) -> tuple[str, Path]:
    target, _ = app.ingestion().ingest_path(source, role="target")
    result = AnalysisController(app).analyze(AnalysisRequest(target=target, use_cache=False))
    assert result.manifest_path is not None
    return result.run_id, Path(result.manifest_path)


@pytest.mark.parametrize("format", ["html", "json"])
def test_cli_report_generation_and_deterministic_republication(
    make_app: Factory,
    app_root: Path,
    target_file: Path,
    capsys: pytest.CaptureFixture[str],
    format: str,
) -> None:
    app = make_app()
    run_id, path = stored_run(app, target_file)
    original = path.read_bytes()
    original_input = target_file.read_bytes()
    assert cli_main(["report", run_id, "--format", format], root=app_root) == 0
    output = Path(capsys.readouterr().out.strip())
    assert output == app.storage.path("reports", run_id, f"report.{format}")
    before = output.read_bytes()
    assert cli_main(["report", run_id, "--format", format], root=app_root) == 0
    capsys.readouterr()
    assert output.read_bytes() == before
    if format == "json":
        assert before == canonical_report_json(build_report(json.loads(original)))
    else:
        assert before.startswith(b"<!doctype html>")
    assert path.read_bytes() == original and target_file.read_bytes() == original_input
    if os.name == "posix":
        assert output.stat().st_mode & 0o077 == 0
        assert output.parent.stat().st_mode & 0o077 == 0


def test_tampered_and_unrecorded_manifests_are_refused(
    make_app: Factory, target_file: Path
) -> None:
    app = make_app()
    run_id, path = stored_run(app, target_file)
    path.write_bytes(path.read_bytes() + b" ")
    with pytest.raises(ReportError, match="integrity verification"):
        app.report(run_id, format="html")
    assert not app.storage.path("reports", run_id).exists()
    app.database()._conn.execute(
        "UPDATE runs SET manifest_sha256 = NULL WHERE run_id = ?", (run_id,)
    )
    with pytest.raises(ReportError, match="no recorded manifest hash"):
        app.report(run_id, format="json")


@pytest.mark.parametrize("kind", ["objects", "artifacts"])
@pytest.mark.parametrize("damage", ["missing", "corrupt", "wrong_size"])
def test_missing_blob_is_not_reported_as_verified(
    make_app: Factory, target_file: Path, kind: str, damage: str
) -> None:
    app = make_app()
    run_id, path = stored_run(app, target_file)
    manifest = json.loads(path.read_bytes())
    digest = (
        manifest["inputs"][0]["sha256"] if kind == "objects" else manifest["artifacts"][0]["sha256"]
    )
    blob = app.storage.blob_path(kind, digest)
    if damage == "wrong_size":
        manifest["inputs" if kind == "objects" else "artifacts"][0]["size"] += 1
        if kind == "artifacts":
            # Keep the two recorded descriptors consistent so this case reaches the
            # independent comparison with physical bytes, rather than descriptor rejection.
            for output in manifest["outputs"].values():
                if (
                    isinstance(output, dict)
                    and isinstance(output.get("artifact"), dict)
                    and output["artifact"]["sha256"] == digest
                ):
                    output["artifact"]["size"] += 1
        data = json.dumps(manifest).encode()
        path.write_bytes(data)
        app.database()._conn.execute(
            "UPDATE runs SET manifest_sha256 = ? WHERE run_id = ?", (sha256_bytes(data), run_id)
        )
    else:
        # Deliberately damage disposable synthetic bytes only, to verify refusal.
        blob.chmod(0o600)
        if damage == "missing":
            blob.unlink()
        else:
            blob.write_bytes(b"corrupted synthetic data")
    with pytest.raises(ReportError, match="missing or fails hash"):
        app.report(run_id, format="html")
    assert not app.storage.path("reports", run_id).exists()


@pytest.mark.parametrize(
    "run_id", ["../evidence", "..", "/absolute", "C:\\private", "f" * 31, "F" * 32]
)
def test_cli_rejects_paths_and_missing_runs(
    app_root: Path, run_id: str, capsys: pytest.CaptureFixture[str]
) -> None:
    assert cli_main(["report", run_id, "--format", "json"], root=app_root) == 1
    assert "invalid run ID" in capsys.readouterr().err
    assert not (app_root / "ARE").exists()


def test_report_does_not_initialize_missing_runtime(app_root: Path) -> None:
    assert cli_main(["report", "f" * 32], root=app_root) == 1
    assert not (app_root / "ARE").exists()


def test_atomic_failure_preserves_previous_complete_report(
    make_app: Factory, target_file: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    app = make_app()
    run_id, _ = stored_run(app, target_file)
    target = app.report(run_id, format="json")
    before = target.read_bytes()
    replace = Path.replace

    def fail(path: Path, destination: Path) -> Path:
        if destination == target:
            raise PermissionError("PRIVATE_STORAGE_PATH")
        return replace(path, destination)

    monkeypatch.setattr(Path, "replace", fail)
    with pytest.raises(ReportError) as caught:
        app.report(run_id, format="json")
    assert "PRIVATE_STORAGE_PATH" not in str(caught.value)
    assert target.read_bytes() == before and list(target.parent.glob("*.part")) == []


def test_symlink_report_destination_never_overwrites_evidence(
    make_app: Factory, target_file: Path
) -> None:
    app = make_app()
    run_id, path = stored_run(app, target_file)
    original = path.read_bytes()
    directory = app.storage.path("reports", run_id)
    directory.mkdir(mode=0o700)
    try:
        (directory / "report.json").symlink_to(path)
    except OSError as error:
        pytest.skip(f"cannot create symlinks on this account: {error}")
    with pytest.raises(ReportError, match="link"):
        app.report(run_id, format="json")
    assert path.read_bytes() == original


@pytest.mark.parametrize("problem", ["duplicate", "wrong_identity", "lifecycle"])
def test_duplicate_json_keys_and_wrong_run_identity_are_refused(
    make_app: Factory, target_file: Path, problem: str
) -> None:
    app = make_app()
    run_id, path = stored_run(app, target_file)
    if problem == "duplicate":
        data = path.read_bytes().rstrip()[:-1] + b',"status":"COMPLETED"}'
    else:
        source = json.loads(path.read_bytes())
        source["run_id" if problem == "wrong_identity" else "status"] = (
            "f" * 32 if problem == "wrong_identity" else "ABORTED"
        )
        data = json.dumps(source).encode()
    path.write_bytes(data)
    app.database()._conn.execute(
        "UPDATE runs SET manifest_sha256 = ? WHERE run_id = ?", (sha256_bytes(data), run_id)
    )
    with pytest.raises(ReportError):
        generate_report(app.storage, app.database(), run_id, format="html")


def test_hardlinked_destination_preserves_source(make_app: Factory, target_file: Path) -> None:
    app = make_app()
    run_id, path = stored_run(app, target_file)
    before = path.read_bytes()
    directory = app.storage.path("reports", run_id)
    directory.mkdir(mode=0o700)
    os.link(path, directory / "report.json")
    with pytest.raises(ReportError, match="hardlinked"):
        app.report(run_id, format="json")
    assert path.read_bytes() == before


@pytest.mark.skipif(os.name != "nt", reason="actual Windows junction containment test")
def test_windows_junction_cannot_redirect_reports(make_app: Factory, target_file: Path) -> None:
    app = make_app()
    run_id, path = stored_run(app, target_file)
    directory = app.storage.path("reports", run_id)
    command = Path(os.environ["SYSTEMROOT"]) / "System32/cmd.exe"
    subprocess.run(
        [str(command), "/c", "mklink", "/J", str(directory), str(path.parent)],
        check=True,
        capture_output=True,
        shell=False,
        timeout=10,
    )
    try:
        with pytest.raises(ReportError, match="link"):
            app.report(run_id, format="html")
        assert not (path.parent / "report.html").exists()
    finally:
        directory.rmdir()  # remove just the disposable junction, never its target
