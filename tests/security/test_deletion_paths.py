"""Run deletion validates IDs, redirects and ordinary-file ownership on real local files."""

import os
import shutil
import subprocess
from collections.abc import Callable
from pathlib import Path

import pytest

from photoproof.contracts.analysis import AnalysisRequest, InputDescriptor
from photoproof.contracts.exceptions import StorageError
from photoproof.core.bootstrap import Application
from photoproof.core.controller import AnalysisController
from photoproof.core.services.deletion import DeletionService


def setup_run(app: Application):  # type: ignore[no-untyped-def]
    original = app.evidence().ingest(
        b"\xff\xd8\xff synthetic", role="target", original_name="space \u2603.jpg"
    )
    result = AnalysisController(app).analyze(
        AnalysisRequest(
            target=InputDescriptor(role="target", sha256=original.sha256, size=original.size),
            use_cache=False,
        )
    )
    app.database().link_submissions([original.submission_id], result.run_id)
    return result, original


@pytest.mark.parametrize(
    "run_id",
    ["../settings", "/etc/passwd", "a" * 31, "G" * 32, "a" * 32 + "/x", "", "a" * 32 + "\x00"],
)
def test_invalid_run_ids_never_delete(make_app: Callable[..., Application], run_id: str) -> None:
    app = make_app()
    result, record = setup_run(app)
    with pytest.raises(StorageError):
        DeletionService(app).delete(run_id, with_evidence=True)
    assert app.database().get_run(result.run_id)
    assert app.storage.verify_blob("objects", record.sha256)


def test_nonexistent_and_absent_runtime_errors(make_app: Callable[..., Application]) -> None:
    app = make_app(start=False)
    with pytest.raises(StorageError, match="runtime"):
        DeletionService(app).delete("a" * 32)
    app.storage.ensure_layout()
    app.database()
    with pytest.raises(StorageError, match="does not exist"):
        DeletionService(app).delete("a" * 32)


def test_report_redirect_cannot_touch_external_data(
    make_app: Callable[..., Application], tmp_path: Path
) -> None:
    app = make_app()
    result, record = setup_run(app)
    outside = tmp_path / "unrelated reports"
    outside.mkdir()
    marker = outside / "report.html"
    marker.write_text("never touch")
    link = app.storage.path("reports", result.run_id)
    if os.name == "nt":
        executable = shutil.which("cmd.exe")
        assert executable
        subprocess.run(
            [executable, "/c", "mklink", "/J", str(link), str(outside)],
            check=True,
            capture_output=True,
        )
    else:
        link.symlink_to(outside, target_is_directory=True)
    with pytest.raises(StorageError):
        DeletionService(app).delete(result.run_id, with_evidence=True)
    assert marker.read_text() == "never touch"
    assert app.storage.verify_blob("objects", record.sha256)


def test_hardlinked_original_is_preserved(make_app: Callable[..., Application]) -> None:
    app = make_app()
    result, record = setup_run(app)
    path = app.storage.blob_path("objects", record.sha256)
    os.link(path, app.storage.path("reports", "external-alias"))
    with pytest.raises(StorageError):
        DeletionService(app).delete(result.run_id, with_evidence=True)
    assert path.read_bytes().startswith(b"\xff\xd8\xff")


@pytest.mark.skipif(os.name != "nt", reason="native Windows READONLY behavior")
def test_windows_readonly_original_is_not_made_writable(
    make_app: Callable[..., Application],
) -> None:
    app = make_app()
    result, record = setup_run(app)
    path = app.storage.blob_path("objects", record.sha256)
    path.chmod(0o400)
    try:
        with pytest.raises(StorageError):
            DeletionService(app).delete(result.run_id, with_evidence=True)
        assert path.stat().st_file_attributes & 1  # FILE_ATTRIBUTE_READONLY
        assert app.storage.verify_blob("objects", record.sha256)
    finally:
        path.chmod(0o600)  # disposable test fixture only; production never does this


def test_remote_filesystem_rejection_precedes_deletion(
    make_app: Callable[..., Application], monkeypatch
) -> None:  # type: ignore[no-untyped-def]
    app = make_app()
    result, record = setup_run(app)

    def refuse(root: Path) -> None:
        raise StorageError("network runtime unsupported")

    monkeypatch.setattr("photoproof.core.services.deletion.require_local_runtime", refuse)
    with pytest.raises(StorageError, match="network"):
        DeletionService(app).delete(result.run_id, with_evidence=True)
    assert app.storage.verify_blob("objects", record.sha256)
