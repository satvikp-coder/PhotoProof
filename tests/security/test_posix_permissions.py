"""The Windows policy must not change existing POSIX modes or SAFE repair output."""

import os
from collections.abc import Callable

import pytest

from photoproof.contracts.enums import Severity
from photoproof.core.bootstrap import Application
from photoproof.core.health.checks import check_runtime_permissions, default_registry
from photoproof.core.health.repair import run_repairs


@pytest.mark.skipif(os.name != "posix", reason="POSIX permission-mode regression test")
def test_posix_modes_and_repair_output_are_unchanged(make_app: Callable[..., Application]) -> None:
    app = make_app()
    blob = app.storage.put_blob("objects", b"unaltered bytes")
    app.storage.path("logs").chmod(0o777)
    assert check_runtime_permissions(app).severity is Severity.WARNING
    records = run_repairs(app, default_registry(), allow_controlled=False, dry_run=False)
    permission = next(record for record in records if record.check_id == "runtime.permissions")
    assert permission.verified and permission.changes == "restricted to 0700: logs"
    assert app.storage.path("logs").stat().st_mode & 0o777 == 0o700
    path = app.storage.blob_path("objects", blob)
    assert path.stat().st_mode & 0o777 == 0o400 and path.read_bytes() == b"unaltered bytes"
