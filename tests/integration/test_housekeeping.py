"""Retention tests use synthetic evidence and disposable runtime roots only."""

import json
import os
import sqlite3
import time
from collections.abc import Callable
from pathlib import Path

import pytest
from helpers import RESULTS, meta, registry_of
from pydantic import ValidationError

from photoproof.cli import main
from photoproof.contracts.analysis import AnalysisRequest, InputDescriptor
from photoproof.contracts.events import Event, EventType
from photoproof.contracts.exceptions import EventDeliveryError, StorageError
from photoproof.core.bootstrap import Application
from photoproof.core.config.flags import FeatureFlags
from photoproof.core.config.validator import QuotaSettings, RetentionSettings
from photoproof.core.controller import AnalysisController
from photoproof.core.health.checks import check_storage_quota
from photoproof.core.logging.audit import append_audit
from photoproof.core.services.housekeeping import Housekeeping
from photoproof.core.storage.store import sha256_bytes

Factory = Callable[..., Application]
TARGET = InputDescriptor(role="target", sha256="a" * 64, size=1)


def run(app: Application, *, cache: bool = True):  # type: ignore[no-untyped-def]
    return AnalysisController(app).analyze(AnalysisRequest(target=TARGET, use_cache=cache))


def retention(app: Application, **values: int) -> None:
    app.settings = app.settings.model_copy(update={"retention": RetentionSettings(**values)})


def snapshot(root: Path) -> dict[str, bytes]:
    return {p.relative_to(root).as_posix(): p.read_bytes() for p in root.rglob("*") if p.is_file()}


def test_dry_run_is_byte_and_database_pure(make_app: Factory, app_root: Path, capsys) -> None:  # type: ignore[no-untyped-def]
    app = make_app()
    run(app)
    before = snapshot(app.storage.root)
    assert Housekeeping(app).plan().candidates == ()
    assert main(["housekeeping"], root=app_root) == 0
    assert "DRY RUN" in capsys.readouterr().out
    assert snapshot(app.storage.root) == before


def test_cache_apply_audit_and_repeat(make_app: Factory) -> None:
    app = make_app()
    run(app)
    retention(app, cache_days=0)
    service = Housekeeping(app)
    plan = service.plan(now=time.time() + 1)
    assert not plan.blocked and len(plan.candidates) == 1
    with pytest.raises(StorageError, match="authorization"):
        service.apply(plan)
    assert len(service.apply(plan, authorized=True)) == 1
    assert app.database()._conn.execute("SELECT count(*) FROM cache_entries").fetchone()[0] == 0
    records = [
        json.loads(line)
        for line in app.storage.path("logs", "audit.jsonl").read_bytes().splitlines()
    ]
    events = [r for r in records if r["type"] == "HOUSEKEEPING_APPLIED"]
    assert [e["data"]["outcome"] for e in events] == ["intent", "deleted"]
    assert service.apply(service.plan(), authorized=True) == ()


def test_shared_references_and_originals_are_protected(make_app: Factory) -> None:
    app = make_app()
    first = run(app, cache=False)
    second = run(app, cache=False)
    original = app.storage.put_blob("objects", b"original")
    orphan = app.storage.put_blob("artifacts", b"disposable")
    retention(app, artifact_days=0)
    service = Housekeeping(app)
    plan = service.plan(now=time.time() + 1)
    assert not plan.blocked
    assert [c.sha256 for c in plan.candidates] == [orphan]
    service.apply(plan, authorized=True)
    assert app.storage.verify_blob("objects", original)
    for result in (first, second):
        manifest = app.storage.read_json("manifests", f"{result.run_id}.json")
        assert isinstance(manifest, dict)
        for ref in manifest["artifacts"]:
            assert app.storage.verify_blob("artifacts", ref["sha256"])


def test_expired_runs_retire_metadata_then_release_orphans(make_app: Factory) -> None:
    app = make_app()
    result = run(app, cache=False)
    original = app.storage.put_blob("objects", b"original")
    retention(app, run_days=0, artifact_days=0)
    service = Housekeeping(app)
    plan = service.plan(now=time.time() + 1)
    assert [c.data_class for c in plan.candidates] == ["run"]
    service.apply(plan, authorized=True)
    assert app.database().get_run(result.run_id) is None
    assert not app.storage.path("manifests", f"{result.run_id}.json").exists()
    plan = service.plan(now=time.time() + 1)
    assert [c.data_class for c in plan.candidates] == ["artifact"]
    service.apply(plan, authorized=True)
    assert app.storage.verify_blob("objects", original)


@pytest.mark.parametrize("damage", ["missing", "bad_json", "mismatch", "references"])
def test_uncertain_graph_prevents_any_deletion(make_app: Factory, damage: str) -> None:
    app = make_app()
    result = run(app, cache=False)
    path = app.storage.path("manifests", f"{result.run_id}.json")
    if damage == "missing":
        path.unlink()
    elif damage == "bad_json":
        path.write_bytes(b"{")
    elif damage == "mismatch":
        path.write_bytes(b"{}")
    else:
        app.database()._conn.execute("DELETE FROM artifacts")
    orphan = app.storage.put_blob("artifacts", b"orphan")
    retention(app, artifact_days=0)
    plan = Housekeeping(app).plan()
    assert plan.blocked and not plan.candidates
    assert app.storage.verify_blob("artifacts", orphan)


def test_live_run_blocks_application_and_changed_plan(make_app: Factory) -> None:
    app = make_app()
    app.storage.put_blob("artifacts", b"orphan")
    retention(app, artifact_days=0)
    service = Housekeeping(app)
    plan = service.plan(now=time.time() + 1)
    app.database().create_run("a" * 32, mode="single", profile="standard", cache_key=None)
    assert service.plan().blocked
    with pytest.raises(StorageError, match="changed"):
        service.apply(plan, authorized=True)
    assert len(list(app.storage.iter_blobs("artifacts"))) == 1


def test_audit_failure_prevents_unlink(make_app: Factory) -> None:
    app = make_app()
    digest = app.storage.put_blob("artifacts", b"orphan")
    retention(app, artifact_days=0)

    def fail(event: Event) -> None:
        raise OSError("audit unavailable")

    app.bus.subscribe(fail, EventType.HOUSEKEEPING_APPLIED, critical=True)
    service = Housekeeping(app)
    with pytest.raises(EventDeliveryError):
        service.apply(service.plan(now=time.time() + 1), authorized=True)
    assert app.storage.verify_blob("artifacts", digest)


def test_permission_failure_preserves_later_candidates(make_app: Factory, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    app = make_app()
    for data in (b"one", b"two"):
        app.storage.put_blob("artifacts", data)
    retention(app, artifact_days=0)
    service = Housekeeping(app)
    plan = service.plan(now=time.time() + 1)
    original_unlink = Path.unlink
    failed_path = app.storage.path(plan.candidates[1].relative_path)

    def deny(path, *args, **kwargs):  # type: ignore[no-untyped-def]
        if path == failed_path:
            raise PermissionError("denied")
        return original_unlink(path, *args, **kwargs)

    monkeypatch.setattr(Path, "unlink", deny)
    with pytest.raises(StorageError, match="incomplete"):
        service.apply(plan, authorized=True)
    assert not app.storage.path(plan.candidates[0].relative_path).exists()
    assert failed_path.exists()
    assert app.database().integrity_problems() == []


@pytest.mark.parametrize("size,expected", [(0, "ok"), (838861, "warning"), (1048576, "error")])
def test_quota_thresholds(make_app: Factory, size: int, expected: str) -> None:
    app = make_app(start=False)
    app.storage.ensure_layout()
    app.settings = app.settings.model_copy(update={"quotas": QuotaSettings(max_total_mb=1)})
    app.storage.path("reports", "synthetic").write_bytes(b"x" * size)
    assert check_storage_quota(app).severity.value == expected
    app.settings = app.settings.model_copy(update={"quotas": QuotaSettings()})
    assert check_storage_quota(app).ok


@pytest.mark.parametrize(
    "sizes,failed",
    [
        ([1048576], False),
        ([1048577], True),
        ([524288, 524288], False),
        ([524288, 524289], True),
        ([600000, 600000], True),
    ],
)
def test_cumulative_artifact_quota(
    make_app: Factory, tmp_path: Path, sizes: list[int], failed: bool
) -> None:
    app = make_app()
    app.registry = registry_of(tmp_path, meta("p.a"))
    app.flags = FeatureFlags.build({}, plugin_ids=app.registry.known_ids, unknown="error")
    app.settings = app.settings.model_copy(update={"quotas": QuotaSettings(max_run_artifacts_mb=1)})

    def produce(ctx):  # type: ignore[no-untyped-def]
        for index, size in enumerate(sizes):
            ctx.write_artifact(f"{index}.bin", b"x" * size, "application/octet-stream")
        return {"p.a.out": 1}

    RESULTS["p.a"] = produce
    try:
        result = run(app, cache=False)
    finally:
        RESULTS.clear()
    assert (result.plugins[0].failure_category == "resource_limit") is failed
    assert (result.plugins[0].state == "SUCCESS") is not failed
    if sizes == [1048577]:
        assert list(app.storage.iter_blobs("artifacts")) == []


def test_rotation_and_crash_tail_preserved(tmp_path: Path) -> None:
    path = tmp_path / "audit.jsonl"
    for index in range(30):
        append_audit(path, {"index": index}, max_bytes=40)
    segments = sorted(tmp_path.glob("audit.jsonl.*"), key=lambda p: int(p.suffix[1:]))
    values = [
        json.loads(line)["index"]
        for segment in [*segments, path]
        for line in segment.read_bytes().splitlines()
    ]
    assert values == list(range(30))
    path.write_bytes(b'{"partial":')
    with pytest.raises(StorageError, match="incomplete"):
        append_audit(path, {"next": 1})
    assert path.read_bytes() == b'{"partial":'


@pytest.mark.parametrize(
    "model,values",
    [
        (RetentionSettings, {"cache_days": -1}),
        (RetentionSettings, {"evidence_days": 1}),
        (RetentionSettings, {"run_days": True}),
        (QuotaSettings, {"max_total_mb": 0}),
        (QuotaSettings, {"warn_at_percent": 100}),
        (QuotaSettings, {"warn_at_percent": 0}),
    ],
)
def test_strict_settings(model, values) -> None:  # type: ignore[no-untyped-def]
    with pytest.raises(ValidationError):
        model(**values)


def test_hardlink_candidate_is_rejected(make_app: Factory) -> None:
    app = make_app()
    digest = app.storage.put_blob("artifacts", b"linked")
    target = app.storage.blob_path("artifacts", digest)
    os.link(target, app.storage.path("reports", "alias"))
    retention(app, artifact_days=0)
    plan = Housekeeping(app).plan()
    assert plan.blocked and not plan.candidates
    assert target.read_bytes() == b"linked"


def test_exclusive_database_reservation_rejects_artifact_guard(make_app: Factory) -> None:
    app = make_app()
    with sqlite3.connect(app.database_path, timeout=0.05) as maintenance:
        maintenance.execute("BEGIN EXCLUSIVE")
        with (
            sqlite3.connect(app.database_path, timeout=0.05) as producer,
            pytest.raises(sqlite3.OperationalError, match="locked"),
        ):
            producer.execute("BEGIN IMMEDIATE")


def test_corrupt_orphan_is_never_cleaned(make_app: Factory) -> None:
    app = make_app()
    digest = app.storage.put_blob("artifacts", b"original-derived")
    path = app.storage.blob_path("artifacts", digest)
    path.chmod(0o600)
    path.write_bytes(b"corrupt")
    retention(app, artifact_days=0)
    plan = Housekeeping(app).plan()
    assert plan.blocked and not plan.candidates
    assert sha256_bytes(path.read_bytes()) != digest


def test_absent_runtime_dry_run_creates_nothing(app_root: Path) -> None:
    assert not (app_root / "ARE").exists()
    assert main(["housekeeping"], root=app_root) == 1
    assert not (app_root / "ARE").exists()


def test_cli_apply_uses_explicit_retention(make_app: Factory, app_root: Path, capsys) -> None:  # type: ignore[no-untyped-def]
    from helpers import update_settings

    app = make_app()
    digest = app.storage.put_blob("artifacts", b"cli-derived")
    update_settings(app_root, retention={"artifact_days": 0})
    assert main(["housekeeping"], root=app_root) == 0
    assert app.storage.verify_blob("artifacts", digest)
    assert main(["housekeeping", "--apply"], root=app_root) == 0
    assert not app.storage.blob_path("artifacts", digest).exists()
    assert "Deleted 1" in capsys.readouterr().out


def test_quota_health_never_applies_retention(make_app: Factory, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    app = make_app()
    digest = app.storage.put_blob("artifacts", b"quota-does-not-authorize-deletion")
    retention(app, artifact_days=0)

    def forbidden(*args, **kwargs):  # type: ignore[no-untyped-def]
        raise AssertionError("preflight attempted housekeeping")

    monkeypatch.setattr(Housekeeping, "apply", forbidden)
    from photoproof.core.health.checks import default_registry
    from photoproof.core.health.repair import preflight

    preflight(app, default_registry(deep=False))
    assert app.storage.verify_blob("artifacts", digest)


def test_outcome_audit_failure_is_not_success(make_app: Factory) -> None:
    app = make_app()
    digest = app.storage.put_blob("artifacts", b"audit-outcome-failure")
    retention(app, artifact_days=0)

    def fail_outcome(event: Event) -> None:
        if event.data["outcome"] == "deleted":
            raise OSError("outcome unavailable")

    app.bus.subscribe(fail_outcome, EventType.HOUSEKEEPING_APPLIED, critical=True)
    service = Housekeeping(app)
    with pytest.raises(EventDeliveryError):
        service.apply(service.plan(), authorized=True)
    assert not app.storage.blob_path("artifacts", digest).exists()
    records = [
        json.loads(line)
        for line in app.storage.path("logs", "audit.jsonl").read_bytes().splitlines()
    ]
    assert any(r["data"].get("outcome") == "intent" for r in records)


def test_swallowed_quota_exception_still_fails_plugin(make_app: Factory, tmp_path: Path) -> None:
    app = make_app()
    app.registry = registry_of(tmp_path, meta("p.a"))
    app.flags = FeatureFlags.build({}, plugin_ids=app.registry.known_ids, unknown="error")
    app.settings = app.settings.model_copy(update={"quotas": QuotaSettings(max_run_artifacts_mb=1)})

    def swallow(ctx):  # type: ignore[no-untyped-def]
        from photoproof.contracts.exceptions import ResourceLimitError

        try:
            ctx.write_artifact("oversized", b"x" * 1048577, "application/octet-stream")
        except ResourceLimitError:
            return {"p.a.out": 1}
        raise AssertionError("quota was not enforced")

    RESULTS["p.a"] = swallow
    try:
        result = run(app, cache=False)
    finally:
        RESULTS.clear()
    assert result.plugins[0].failure_category == "resource_limit"


def test_quota_usage_deduplicates_hardlinks_and_reports_unknown_access(make_app: Factory) -> None:
    from photoproof.core.storage.managed import usage

    app = make_app(start=False)
    app.storage.ensure_layout()
    path = app.storage.path("reports", "one")
    path.write_bytes(b"123")
    os.link(path, app.storage.path("reports", "two"))
    assert usage(app.storage.root) == 3


def test_future_timestamps_do_not_expire(make_app: Factory) -> None:
    app = make_app()
    digest = app.storage.put_blob("artifacts", b"future")
    path = app.storage.blob_path("artifacts", digest)
    future = time.time() + 86400
    os.utime(path, (future, future))
    retention(app, artifact_days=0)
    assert Housekeeping(app).plan().candidates == ()


def test_pending_submission_protects_post_analysis_linking(make_app: Factory) -> None:
    app = make_app()
    result = run(app, cache=False)
    receipt = app.evidence().ingest(b"\xff\xd8\xff synthetic", role="target")
    retention(app, run_days=0)
    service = Housekeeping(app)
    plan = service.plan()
    assert plan.blocked and not plan.candidates
    assert app.database().get_run(result.run_id) is not None
    app.database().link_submissions([receipt.submission_id], result.run_id)
    assert any(candidate.run_id == result.run_id for candidate in service.plan().candidates)


def test_candidate_changed_during_intent_is_not_deleted(make_app: Factory) -> None:
    app = make_app()
    digest = app.storage.put_blob("artifacts", b"derived")
    target = app.storage.blob_path("artifacts", digest)
    retention(app, artifact_days=0)

    def change(event: Event) -> None:
        if event.data["outcome"] == "intent":
            target.chmod(0o600)
            target.write_bytes(b"changed")

    app.bus.subscribe(change, EventType.HOUSEKEEPING_APPLIED)
    service = Housekeeping(app)
    with pytest.raises(StorageError, match="changed during audit"):
        service.apply(service.plan(), authorized=True)
    assert target.read_bytes() == b"changed"
