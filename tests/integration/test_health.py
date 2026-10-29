import os
import time
from collections.abc import Callable

from photoproof.contracts.enums import Repairability, Severity
from photoproof.core.bootstrap import Application
from photoproof.core.health.checks import default_registry
from photoproof.core.health.repair import preflight, run_repairs

Factory = Callable[..., Application]


def _by_id(app: Application) -> dict[str, object]:
    return {r.check_id: r for r in default_registry().run_all(app)}


def test_missing_runtime_directories_detected_and_repaired(make_app: Factory) -> None:
    app = make_app(start=False)
    result = _by_id(app)["runtime.directories"]
    assert result.severity is Severity.ERROR and result.repair.category is Repairability.SAFE  # type: ignore[attr-defined]
    report = preflight(app, default_registry())
    assert report.passed, [r.to_dict() for r in report.blocking]
    assert any(r.check_id == "runtime.directories" and r.verified for r in report.repairs)


def test_repair_dry_run_changes_nothing(make_app: Factory) -> None:
    app = make_app(start=False)
    records = run_repairs(app, default_registry(), allow_controlled=True, dry_run=True)
    assert [r.outcome for r in records if r.check_id == "runtime.directories"] == ["planned"]
    assert not app.storage.root.exists()


def test_stale_temp_files_cleaned(make_app: Factory) -> None:
    app = make_app()
    stale = app.storage.path("tmp") / "old.part"
    stale.write_text("x")
    past = time.time() - 72 * 3600
    os.utime(stale, (past, past))
    assert _by_id(app)["runtime.stale_temp"].severity is Severity.WARNING  # type: ignore[attr-defined]
    records = run_repairs(app, default_registry(), allow_controlled=False, dry_run=False)
    assert any(r.check_id == "runtime.stale_temp" and r.verified for r in records)
    assert not stale.exists()


def test_corrupt_evidence_is_never_repaired(make_app: Factory) -> None:
    app = make_app()
    sha = app.storage.put_blob("objects", b"original evidence")
    path = app.storage.blob_path("objects", sha)
    path.chmod(0o600)
    path.write_bytes(b"modified")
    result = _by_id(app)["storage.integrity"]
    assert result.severity is Severity.CRITICAL and result.repair.category is Repairability.MANUAL  # type: ignore[attr-defined]
    records = run_repairs(app, default_registry(), allow_controlled=True, dry_run=False)
    assert [r.outcome for r in records if r.check_id == "storage.integrity"] == ["manual"]
    assert path.read_bytes() == b"modified"


def test_invalid_cache_entry_repaired(make_app: Factory) -> None:
    app = make_app()
    entry = app.storage.root / "cache" / "runs" / ("c" * 64 + ".json")
    entry.parent.mkdir(parents=True)
    entry.write_text("garbage")
    assert _by_id(app)["cache.integrity"].severity is Severity.WARNING  # type: ignore[attr-defined]
    run_repairs(app, default_registry(), allow_controlled=False, dry_run=False)
    assert not entry.exists()


def test_broken_plugins_reported(make_app: Factory) -> None:
    app = make_app("mockplugins.broken")
    assert _by_id(app)["plugins.graph"].severity is Severity.WARNING  # type: ignore[attr-defined]


def test_preflight_blocks_on_unrepairable_error(make_app: Factory, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    app = make_app()
    monkeypatch.setattr("photoproof.core.health.checks.distribution_version", lambda _: "9.9.9")
    report = preflight(app, default_registry())
    assert not report.passed and report.blocking[0].check_id == "application.version"


def test_health_failures_persisted(make_app: Factory) -> None:
    app = make_app()
    (app.storage.root / "tmp" / "x").write_text("x")
    os.utime(app.storage.root / "tmp" / "x", (0, 0))
    update = preflight(app, default_registry())
    assert update.passed
