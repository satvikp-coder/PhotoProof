"""Maintenance refuses redirects and never treats evidence as disposable."""

import os
import shutil
import subprocess
from collections.abc import Callable
from pathlib import Path

import pytest

from photoproof.contracts.exceptions import StorageError
from photoproof.core.bootstrap import Application
from photoproof.core.config.validator import RetentionSettings
from photoproof.core.services.housekeeping import Housekeeping
from photoproof.core.storage.managed import checked_path


def test_redirected_artifact_directory_is_blocked(
    make_app: Callable[..., Application], tmp_path: Path
) -> None:
    app = make_app()
    outside = tmp_path / "untouched"
    outside.mkdir()
    marker = outside / ("a" * 64)
    marker.write_bytes(b"never delete")
    link = app.storage.path("artifacts", "aa")
    if os.name == "nt":
        executable = shutil.which("cmd.exe")
        assert executable is not None
        subprocess.run(
            [executable, "/c", "mklink", "/J", str(link), str(outside)],
            check=True,
            capture_output=True,
        )
    else:
        link.symlink_to(outside, target_is_directory=True)
    app.settings = app.settings.model_copy(update={"retention": RetentionSettings(artifact_days=0)})
    plan = Housekeeping(app).plan()
    assert plan.blocked and not plan.candidates
    assert marker.read_bytes() == b"never delete"


def test_traversal_rejected(tmp_path: Path) -> None:
    with pytest.raises(StorageError):
        checked_path(tmp_path, "../outside")


def test_evidence_hash_is_not_deleted_even_if_derived_copy_exists(
    make_app: Callable[..., Application],
) -> None:
    app = make_app()
    digest = app.storage.put_blob("objects", b"same")
    app.storage.put_blob("artifacts", b"same")
    app.settings = app.settings.model_copy(update={"retention": RetentionSettings(artifact_days=0)})
    service = Housekeeping(app)
    assert not service.plan().candidates
    service.apply(service.plan(), authorized=True)
    assert app.storage.verify_blob("objects", digest)
    assert app.storage.verify_blob("artifacts", digest)
