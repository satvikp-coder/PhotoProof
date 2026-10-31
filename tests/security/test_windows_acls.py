"""Real Windows DACL/attribute integration tests. Every path is a disposable fixture."""

import ctypes
import json
import os
import subprocess
from collections.abc import Callable
from pathlib import Path
from unittest.mock import patch

import pytest
from helpers import make_blob_writable

from photoproof.contracts.enums import Repairability, Severity
from photoproof.contracts.exceptions import StorageError
from photoproof.core.bootstrap import Application
from photoproof.core.health.checks import check_runtime_permissions, default_registry
from photoproof.core.health.repair import run_repairs
from photoproof.core.storage import windows_acl as acl
from photoproof.core.storage.store import RUNTIME_DIRS, RuntimeStorage

pytestmark = pytest.mark.skipif(os.name != "nt", reason="requires real Windows security APIs")
Factory = Callable[..., Application]


def _grant(path: Path, principal: str, rights: str) -> None:
    executable = Path(os.environ["SYSTEMROOT"]) / "System32" / "icacls.exe"
    subprocess.run(
        [str(executable), str(path), "/grant:r", f"*{principal}:{rights}"],
        shell=False,
        check=True,
        capture_output=True,
    )


def test_native_directory_acl_unicode_and_idempotence(tmp_path: Path) -> None:
    store = RuntimeStorage(tmp_path / "runtime space Ω 数据")
    assert store.ensure_layout() == list(RUNTIME_DIRS)
    expected = {acl.current_user_sid(), acl.SYSTEM_SID, acl.ADMINISTRATORS_SID}
    for path in [store.root, *(store.root / name for name in RUNTIME_DIRS)]:
        actual = acl.inspect(path)
        assert actual.protected and actual.owner == acl.current_user_sid()
        assert {ace.sid for ace in actual.aces} == expected
        assert all(ace.mask == acl.FULL_CONTROL and ace.flags == 3 for ace in actual.aces)
    assert store.ensure_layout() == [] and store.insecure_dirs() == []

    # Independent .NET ACL readback: no localized username assumptions in assertions.
    executable = Path(os.environ["SYSTEMROOT"]) / "System32/WindowsPowerShell/v1.0/powershell.exe"
    script = (
        "$ErrorActionPreference = 'Stop'; "
        "$a = [System.IO.Directory]::GetAccessControl($env:ACL_TEST_PATH); "
        "@{protected=$a.AreAccessRulesProtected; sids=@($a.Access | ForEach-Object { "
        "$_.IdentityReference.Translate([System.Security.Principal.SecurityIdentifier]).Value "
        "})} | ConvertTo-Json -Compress"
    )
    result = subprocess.run(
        [str(executable), "-NoProfile", "-NonInteractive", "-Command", script],
        env={**os.environ, "ACL_TEST_PATH": str(store.root)},
        shell=False,
        capture_output=True,
        text=True,
        check=True,
    )
    independent = json.loads(result.stdout)
    assert independent["protected"] and set(independent["sids"]) == expected


def test_trusted_ancestor_junction_is_resolved_once(tmp_path: Path) -> None:
    target = tmp_path / "trusted location"
    target.mkdir()
    alias = tmp_path / "configured alias"
    executable = Path(os.environ["SYSTEMROOT"]) / "System32/cmd.exe"
    subprocess.run(
        [str(executable), "/c", "mklink", "/J", str(alias), str(target)],
        check=True,
        capture_output=True,
        shell=False,
        timeout=10,
    )
    try:
        store = RuntimeStorage(alias / "ARE")
        assert store.root == (target / "ARE").resolve()
        store.ensure_layout()
        digest = store.put_blob("objects", b"synthetic through trusted ancestor")
        assert store.verify_blob("objects", digest) and store.insecure_dirs() == []
        _grant(store.root / "logs", "S-1-1-0", "R")
        before = acl.inspect(store.root / "logs")
        with pytest.raises(StorageError, match="insecure runtime ACL"):
            store.ensure_layout()
        assert acl.inspect(store.root / "logs") == before
        assert "logs" in store.insecure_dirs()
        store.restrict_permissions(["logs"])
        assert store.insecure_dirs() == []
    finally:
        alias.rmdir()


@pytest.mark.parametrize("principal", ["S-1-1-0", "S-1-5-32-545", "S-1-5-11"])
def test_unauthorized_aces_detected_and_safe_repair_removes_them(
    make_app: Factory, principal: str
) -> None:
    app = make_app()
    target = app.storage.root / "logs"
    data = target / "keep.txt"
    data.write_bytes(b"preserve these bytes")
    _grant(target, principal, "(OI)(CI)R")
    assert "logs" in app.storage.insecure_dirs()
    assert any(ace.sid == principal for ace in acl.inspect(data).aces)  # inherited broad access
    result = check_runtime_permissions(app)
    assert result.severity is Severity.ERROR and result.repair is not None
    assert result.repair.category is Repairability.SAFE
    records = run_repairs(app, default_registry(), allow_controlled=False, dry_run=False)
    record = next(r for r in records if r.check_id == "runtime.permissions")
    assert record.outcome == "applied" and record.verified is True
    assert app.storage.insecure_dirs() == [] and data.read_bytes() == b"preserve these bytes"
    assert not any(ace.sid == principal for ace in acl.inspect(data).aces)
    assert all(
        r.check_id != "runtime.permissions"
        for r in run_repairs(app, default_registry(), allow_controlled=False, dry_run=False)
    )


def test_root_existing_files_and_nested_directories_are_checked(make_app: Factory) -> None:
    app = make_app()
    nested = app.storage.root / "cache/runs"
    nested.mkdir()
    item = nested / "keep.json"
    item.write_text("{}")
    _grant(app.storage.root, "S-1-1-0", "(OI)(CI)R")
    _grant(item, "S-1-5-11", "R")
    insecure = app.storage.insecure_dirs()
    assert "." in insecure and "cache/runs/keep.json" in insecure
    app.storage.restrict_permissions(insecure)
    assert app.storage.insecure_dirs() == [] and item.read_text() == "{}"


def test_blob_readonly_attribute_and_dacl_both_enforced(tmp_path: Path) -> None:
    store = RuntimeStorage(tmp_path / "ARE")
    store.ensure_layout()
    digest = store.put_blob("objects", b"original evidence")
    path = store.blob_path("objects", digest)
    actual = acl.inspect(path)
    assert actual.attributes & acl.READONLY and actual.protected
    assert all(ace.mask == acl.FILE_READ and not ace.mask & 0x116 for ace in actual.aces)
    assert path.read_bytes() == b"original evidence"
    with pytest.raises(PermissionError):
        path.write_bytes(b"must not succeed")
    with pytest.raises(PermissionError):
        path.chmod(0o600)  # write attributes is not normally granted either
    assert store.put_blob("objects", b"original evidence") == digest
    assert store.verify_blob("objects", digest) and store.insecure_dirs() == []


def test_readonly_dacl_blocks_write_even_without_dos_attribute(tmp_path: Path) -> None:
    path = tmp_path / "evidence"
    path.write_bytes(b"unchanged")
    # A pre-existing handle retains its granted rights; the guarantee is for new opens.
    with acl._handle(path, access=0x20180) as handle:  # only read/security/write-attribute rights
        acl.restrict_file(path, readonly=True)
        basic = acl._BasicInfo()
        basic.attributes = acl.inspect(path).attributes & ~acl.READONLY
        api = acl._api()
        assert api.kernel.SetFileInformationByHandle(
            handle, 0, ctypes.byref(basic), ctypes.sizeof(basic)
        )
        with pytest.raises(PermissionError):
            path.open("r+b")
        assert path.read_bytes() == b"unchanged"
        # Missing attribute on an already sealed DACL cannot be fixed by silently
        # granting write-attribute access. Report failure and leave rights intact.
        with pytest.raises(StorageError, match="WinError 5"):
            acl.restrict_file(path, readonly=True)
        assert all(ace.mask == acl.FILE_READ for ace in acl.inspect(path).aces)
        basic.attributes |= acl.READONLY
        assert api.kernel.SetFileInformationByHandle(
            handle, 0, ctypes.byref(basic), ctypes.sizeof(basic)
        )


def test_failed_acl_write_leaves_prior_acl_and_reports_failed_repair(make_app: Factory) -> None:
    app = make_app()
    target = app.storage.root / "reports"
    _grant(target, "S-1-1-0", "R")
    before = acl.inspect(target)
    with patch.object(acl._api().security, "SetSecurityInfo", return_value=5):
        records = run_repairs(app, default_registry(), allow_controlled=False, dry_run=False)
    record = next(r for r in records if r.check_id == "runtime.permissions")
    assert record.outcome == "failed" and "WinError 5" in record.changes
    assert acl.inspect(target) == before and not check_runtime_permissions(app).ok


def test_success_return_without_enforcement_fails_readback(tmp_path: Path) -> None:
    path = tmp_path / "private"
    acl.ensure_directory(path)
    _grant(path, "S-1-1-0", "R")
    with (
        patch.object(acl._api().security, "SetSecurityInfo", return_value=0),
        pytest.raises(StorageError, match="readback failed"),
    ):
        acl.ensure_directory(path)


def test_unsupported_volume_warns_but_evidence_writes_fail_closed(make_app: Factory) -> None:
    app = make_app(start=False)
    with patch.object(
        acl, "require_supported_volume", side_effect=acl.UnsupportedACL("no persistent ACLs")
    ):
        app.storage.ensure_layout()
        result = check_runtime_permissions(app)
        assert result.severity is Severity.WARNING and result.repair is not None
        assert result.repair.category is Repairability.MANUAL
        with pytest.raises(acl.UnsupportedACL):
            app.storage.put_blob("objects", b"must not be written")
        assert not list(app.storage.iter_blobs("objects"))


def test_volume_checks_reject_unc_mapped_and_missing_acl_support(tmp_path: Path) -> None:
    with pytest.raises(acl.UnsupportedACL, match="network/UNC"):
        acl.require_supported_volume(Path(r"\\server\share\ARE"))
    with (
        patch.object(acl._api().kernel, "GetDriveTypeW", return_value=4),
        pytest.raises(acl.UnsupportedACL, match="mapped network"),
    ):
        acl.require_supported_volume(tmp_path)

    def no_acl_support(*args: object) -> int:
        args[5]._obj.value = 0  # type: ignore[attr-defined]
        return 1

    with (
        patch.object(acl._api().kernel, "GetVolumeInformationW", side_effect=no_acl_support),
        pytest.raises(acl.UnsupportedACL, match="persistent ACLs"),
    ):
        acl.require_supported_volume(tmp_path)


def test_permission_inspection_failure_is_not_healthy(make_app: Factory) -> None:
    app = make_app()
    with patch.object(acl._api().security, "GetSecurityInfo", return_value=5):
        result = check_runtime_permissions(app)
    assert result.severity is Severity.ERROR and "WinError 5" in str(result.details)


def test_hardlinked_data_is_not_repermissioned(tmp_path: Path) -> None:
    outside = tmp_path / "unrelated"
    outside.write_bytes(b"not runtime data")
    linked = tmp_path / "link"
    os.link(outside, linked)
    with pytest.raises(StorageError, match="hard links"):
        acl.restrict_file(linked, readonly=True)
    assert outside.read_bytes() == b"not runtime data"


def test_foreign_owner_never_implicitly_taken_over(tmp_path: Path) -> None:
    path = tmp_path / "private"
    acl.ensure_directory(path)
    before = acl.inspect(path)
    foreign = acl.ACLSnapshot(
        "S-1-5-21-111-222-333-444", before.protected, before.aces, before.attributes
    )
    with (
        patch.object(acl, "_snapshot", return_value=foreign),
        pytest.raises(StorageError, match="ownership migration"),
    ):
        acl.ensure_directory(path)
    assert acl.inspect(path) == before


def test_owner_can_explicitly_change_permissions_but_hash_semantics_remain(tmp_path: Path) -> None:
    store = RuntimeStorage(tmp_path / "ARE")
    digest = store.put_blob("objects", b"evidence")
    path = store.blob_path("objects", digest)
    make_blob_writable(path)
    path.write_bytes(b"tampered")
    with pytest.raises(StorageError, match="fails verification"):
        store.put_blob("objects", b"evidence")
    assert path.read_bytes() == b"tampered" and not store.verify_blob("objects", digest)


def test_creation_denial_does_not_fall_back_to_inherited_permissions(tmp_path: Path) -> None:
    path = tmp_path / "must stay absent"

    def denied(*_args: object) -> int:
        ctypes.set_last_error(5)
        return 0

    with (
        patch.object(acl._api().kernel, "CreateDirectoryW", side_effect=denied),
        pytest.raises(StorageError, match="WinError 5"),
    ):
        acl.ensure_directory(path)
    assert not path.exists()


def test_null_dacl_is_reported_and_safely_replaced(make_app: Factory) -> None:
    app = make_app()
    path = app.storage.root / "reports"
    with acl._handle(path, access=acl.MAXIMUM_ALLOWED) as handle:
        assert (
            acl._api().security.SetSecurityInfo(
                handle, 1, acl.PROTECTED_DACL, None, None, None, None
            )
            == 0
        )
    assert acl.inspect(path).null_dacl and "reports" in app.storage.insecure_dirs()
    app.storage.restrict_permissions(app.storage.insecure_dirs())
    assert not acl.inspect(path).null_dacl and check_runtime_permissions(app).ok


def test_junctions_never_redirect_permission_repair(tmp_path: Path) -> None:
    store = RuntimeStorage(tmp_path / "ARE")
    store.ensure_layout()
    unrelated = tmp_path / "unrelated target"
    unrelated.mkdir()
    (unrelated / "keep.txt").write_bytes(b"unrelated bytes")
    before = acl.inspect(unrelated)
    junction = store.root / "logs/redirect"
    executable = Path(os.environ["SYSTEMROOT"]) / "System32/WindowsPowerShell/v1.0/powershell.exe"
    subprocess.run(
        [
            str(executable),
            "-NoProfile",
            "-NonInteractive",
            "-Command",
            "New-Item -ItemType Junction -Path $env:ACL_LINK_PATH "
            "-Target $env:ACL_LINK_TARGET | Out-Null",
        ],
        env={**os.environ, "ACL_LINK_PATH": str(junction), "ACL_LINK_TARGET": str(unrelated)},
        shell=False,
        check=True,
        capture_output=True,
    )
    try:
        assert junction.lstat().st_file_attributes & acl.REPARSE_POINT
        with pytest.raises(StorageError, match="reparse"):
            store.insecure_dirs()
        with pytest.raises(StorageError, match="reparse"):
            store.restrict_permissions(["logs/redirect"])
        _grant(store.root, "S-1-1-0", "R")  # force an actual root ACL update
        with pytest.raises(StorageError, match="insecure runtime ACL"):
            store.ensure_layout()  # no silent permission repair during layout creation
        acl.ensure_directory(store.root)  # explicit metadata repair; no child propagation
        assert acl.inspect(unrelated) == before
        assert (unrelated / "keep.txt").read_bytes() == b"unrelated bytes"
    finally:
        junction.rmdir()  # removes this specific test junction, never the target contents


def test_ingested_original_is_sealed_without_changing_submitted_source(
    make_app: Factory,
    tmp_path: Path,
) -> None:
    app = make_app()
    submitted = tmp_path / "submitted original Ω.bin"
    submitted.write_bytes(b"original source bytes")
    before = acl.inspect(submitted)
    record = app.evidence().ingest(submitted, role="target")
    target = app.storage.blob_path("objects", record.sha256)
    assert not acl.problems(acl.inspect(target), directory=False, readonly=True, protected=True)
    assert acl.inspect(submitted) == before and submitted.read_bytes() == b"original source bytes"
    with app.evidence().open(record.sha256) as verified:
        assert verified.read() == b"original source bytes"


def test_unsupported_ingest_refuses_before_creating_staging_files(make_app: Factory) -> None:
    app = make_app()
    with (
        patch.object(
            acl, "require_supported_volume", side_effect=acl.UnsupportedACL("unsupported")
        ),
        patch("photoproof.core.storage.evidence.tempfile.mkstemp") as staging,
        pytest.raises(acl.UnsupportedACL),
    ):
        app.evidence().ingest(b"must not be staged", role="target")
    staging.assert_not_called()
    assert app.database().evidence_hashes() == []


def test_normal_writes_do_not_silently_repair_existing_runtime_acls(make_app: Factory) -> None:
    app = make_app()
    unrelated = app.storage.path("logs")
    _grant(unrelated, "S-1-1-0", "R")
    before = acl.inspect(unrelated)
    digest = app.storage.put_blob("artifacts", b"new private artifact")
    assert acl.inspect(unrelated) == before and "logs" in app.storage.insecure_dirs()
    assert app.storage.verify_blob("artifacts", digest)
    relevant = app.storage.path("artifacts")
    _grant(relevant, "S-1-1-0", "R")
    before = acl.inspect(relevant)
    with pytest.raises(StorageError, match="insecure runtime ACL"):
        app.storage.put_blob("artifacts", b"refuse until repaired")
    assert acl.inspect(relevant) == before and not check_runtime_permissions(app).ok


def test_blob_reuse_reports_broadened_acl_without_silent_repair(make_app: Factory) -> None:
    app = make_app()
    digest = app.storage.put_blob("objects", b"unchanged evidence")
    path = app.storage.blob_path("objects", digest)
    _grant(path, "S-1-1-0", "R")
    before = acl.inspect(path)
    with pytest.raises(StorageError, match="insecure existing blob"):
        app.storage.put_blob("objects", b"unchanged evidence")
    assert acl.inspect(path) == before and path.read_bytes() == b"unchanged evidence"
    app.storage.restrict_permissions(app.storage.insecure_dirs())
    assert not acl.problems(acl.inspect(path), directory=False, readonly=True, protected=True)
