"""Synthetic privacy lifecycles on disposable runtime roots."""

import json
import time
from collections.abc import Callable
from pathlib import Path

import pytest
from helpers import update_settings
from pydantic import ValidationError

from photoproof.cli import main
from photoproof.contracts.analysis import AnalysisRequest, InputDescriptor
from photoproof.contracts.events import Event, EventType
from photoproof.contracts.exceptions import EventDeliveryError, StorageError
from photoproof.core.bootstrap import Application
from photoproof.core.config.validator import RetentionSettings
from photoproof.core.controller import AnalysisController
from photoproof.core.services.deletion import DeletionService
from photoproof.core.services.housekeeping import Housekeeping
from photoproof.core.storage.deletion_journal import Journal, journals, write_journal

Factory = Callable[..., Application]
CONTENT = b"\xff\xd8\xff synthetic private image"


def create_run(
    app: Application,
    *,
    content: bytes = CONTENT,
    name: str = "private photo.jpg",
    cache: bool = False,
):  # type: ignore[no-untyped-def]
    record = app.evidence().ingest(content, role="target", original_name=name)
    target = InputDescriptor(
        role="target", sha256=record.sha256, size=record.size, media_format=record.media_format
    )
    result = AnalysisController(app).analyze(AnalysisRequest(target=target, use_cache=cache))
    app.database().link_submissions([record.submission_id], result.run_id)
    return result, record


def test_default_delete_exact_run_reports_cache_and_unshared_artifacts(make_app: Factory) -> None:
    app = make_app()
    result, record = create_run(app, cache=True)
    other, other_record = create_run(app, content=CONTENT + b"different")
    report_dir = app.storage.path("reports", result.run_id)
    report_dir.mkdir()
    (report_dir / "report.html").write_text("<html>synthetic</html>")
    (report_dir / "report.json").write_text("{}")
    backup = app.storage.path("database", "preserve.bak")
    backup.write_bytes(b"backup copy")
    manifest = app.storage.read_json("manifests", f"{result.run_id}.json")
    assert isinstance(manifest, dict)
    answer = DeletionService(app).delete(result.run_id)
    assert "originals retained" in answer
    assert app.database().get_run(result.run_id) is None
    assert app.database().get_run(other.run_id) is not None
    assert app.storage.verify_blob("objects", record.sha256)
    assert app.storage.verify_blob("objects", other_record.sha256)
    assert app.database().submissions(record.sha256) == []
    assert list(report_dir.iterdir()) == []
    assert backup.read_bytes() == b"backup copy"
    assert (
        app.database()
        ._conn.execute("SELECT count(*) FROM cache_entries WHERE run_id = ?", (result.run_id,))
        .fetchone()[0]
        == 0
    )
    for item in manifest["artifacts"]:
        assert not app.storage.blob_path("artifacts", item["sha256"]).exists()
    assert all(j.phase == "complete" for j in journals(app.storage.root))
    assert "already deleted" in DeletionService(app).delete(result.run_id)
    with pytest.raises(StorageError, match="does not exist"):
        DeletionService(app).delete(result.run_id, with_evidence=True)


def test_explicit_unshared_original_delete_and_receipts(make_app: Factory) -> None:
    app = make_app()
    name = "private Unicode \u2603.jpg"
    result, record = create_run(app, name=name)
    DeletionService(app).delete(result.run_id, with_evidence=True)
    assert not app.storage.blob_path("objects", record.sha256).exists()
    assert record.sha256 not in app.database().evidence_hashes()
    assert app.database().submissions(record.sha256) == []
    audit = app.storage.path("logs", "audit.jsonl").read_text(encoding="utf-8")
    assert name not in audit and "private Unicode" not in audit
    entries = [json.loads(line) for line in audit.splitlines()]
    receipts = [e["data"] for e in entries if e["type"] == "DELETION_APPLIED"]
    assert receipts[0]["outcome"] == "intent" and receipts[-1]["outcome"] == "complete"
    assert any(
        r.get("identity_sha256") == record.sha256 and r["outcome"] == "deleted" for r in receipts
    )
    assert "already deleted" in DeletionService(app).delete(result.run_id, with_evidence=True)


def test_shared_original_refusal_preserves_both_runs(make_app: Factory) -> None:
    app = make_app()
    first, record = create_run(app, name="one.jpg")
    second, _ = create_run(app, name="other name.jpg")
    with pytest.raises(StorageError, match="shared"):
        DeletionService(app).delete(first.run_id, with_evidence=True)
    assert app.database().get_run(first.run_id) and app.database().get_run(second.run_id)
    DeletionService(app).delete(first.run_id)
    assert app.storage.verify_blob("objects", record.sha256)
    assert len(app.database().submissions(record.sha256)) == 1
    manifest = app.storage.read_json("manifests", f"{second.run_id}.json")
    assert isinstance(manifest, dict)
    for item in manifest["artifacts"]:
        assert app.storage.verify_blob("artifacts", item["sha256"])


@pytest.mark.parametrize("pending", [False, True])
def test_multiple_or_pending_submissions_protect_original(make_app: Factory, pending: bool) -> None:
    app = make_app()
    result, record = create_run(app)
    second = app.evidence().ingest(CONTENT, role="target", original_name="second.jpg")
    if not pending:
        app.database().link_submissions([second.submission_id], result.run_id)
    with pytest.raises(StorageError, match="submissions"):
        DeletionService(app).delete(result.run_id, with_evidence=True)
    assert app.storage.verify_blob("objects", record.sha256)


@pytest.mark.parametrize(
    "damage",
    [
        "missing",
        "tampered",
        "missing_submission",
        "wrong_submission",
        "missing_artifact",
        "unknown_object",
    ],
)
def test_uncertain_graph_blocks_all_deletion(make_app: Factory, damage: str) -> None:
    app = make_app()
    result, record = create_run(app)
    path = app.storage.path("manifests", f"{result.run_id}.json")
    if damage == "missing":
        path.unlink()
    elif damage == "tampered":
        path.write_bytes(b"{}")
    elif damage == "missing_submission":
        app.database()._conn.execute("DELETE FROM submissions")
    elif damage == "wrong_submission":
        app.database()._conn.execute("UPDATE submissions SET role = 'reference'")
    elif damage == "missing_artifact":
        app.database()._conn.execute("DELETE FROM artifacts")
    else:
        app.storage.put_blob("objects", b"unregistered original")
    with pytest.raises(StorageError):
        DeletionService(app).delete(result.run_id, with_evidence=True)
    assert app.storage.verify_blob("objects", record.sha256)
    assert app.database().get_run(result.run_id) is not None


def test_active_run_protected(make_app: Factory) -> None:
    app = make_app()
    result, record = create_run(app)
    app.database().create_run("f" * 32, mode="single", profile="standard", cache_key=None)
    with pytest.raises(StorageError, match="idle"):
        DeletionService(app).delete(result.run_id, with_evidence=True)
    assert app.storage.verify_blob("objects", record.sha256)


def test_unknown_reports_cannot_be_recursively_deleted(make_app: Factory) -> None:
    app = make_app()
    result, _ = create_run(app)
    report_dir = app.storage.path("reports", result.run_id)
    report_dir.mkdir()
    (report_dir / "unrelated folder").mkdir()
    with pytest.raises(StorageError, match="unknown run report"):
        DeletionService(app).delete(result.run_id)
    assert (report_dir / "unrelated folder").is_dir()


def test_audit_failure_before_deletion_is_cancelled_and_retryable(make_app: Factory) -> None:
    app = make_app()
    result, record = create_run(app)
    armed = True

    def fail(event: Event) -> None:
        if armed:
            raise OSError("audit sink unavailable")

    app.bus.subscribe(fail, EventType.DELETION_APPLIED, critical=True)
    with pytest.raises(EventDeliveryError):
        DeletionService(app).delete(result.run_id, with_evidence=True)
    assert app.storage.verify_blob("objects", record.sha256)
    assert all(j.phase == "cancelled" for j in journals(app.storage.root))
    armed = False
    DeletionService(app).delete(result.run_id, with_evidence=True)


def test_partial_deletion_blocks_automatic_continuation(make_app: Factory, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    app = make_app()
    result, record = create_run(app)
    original_unlink = Path.unlink
    original = app.storage.blob_path("objects", record.sha256)

    def deny(path, *args, **kwargs):  # type: ignore[no-untyped-def]
        if path == original:
            raise PermissionError("restricted original")
        return original_unlink(path, *args, **kwargs)

    monkeypatch.setattr(Path, "unlink", deny)
    with pytest.raises(StorageError, match="journal"):
        DeletionService(app).delete(result.run_id, with_evidence=True)
    assert app.storage.verify_blob("objects", record.sha256)
    assert any(j.phase == "pending" for j in journals(app.storage.root))
    with pytest.raises(StorageError, match="uncertain deletion intent"):
        DeletionService(app).delete(result.run_id, with_evidence=True)
    assert Housekeeping(app).plan().blocked


def test_existing_uncertain_intent_blocks_new_operation(make_app: Factory) -> None:
    app = make_app()
    result, _ = create_run(app)
    write_journal(app.storage.root, Journal(operation_id="e" * 32, plan_sha256="e" * 64))
    with pytest.raises(StorageError, match="uncertain deletion intent"):
        DeletionService(app).delete(result.run_id)


def test_evidence_retention_default_and_authorized_apply(make_app: Factory) -> None:
    app = make_app()
    result, record = create_run(app)
    DeletionService(app).delete(result.run_id)
    assert not Housekeeping(app).plan(now=time.time() + 86400 * 1000).candidates
    app.settings = app.settings.model_copy(update={"retention": RetentionSettings(evidence_days=1)})
    service = Housekeeping(app)
    plan = service.plan(now=time.time() + 86400 * 2)
    assert [c.data_class for c in plan.candidates] == ["evidence"]
    assert app.storage.verify_blob("objects", record.sha256)
    with pytest.raises(StorageError, match="authorization"):
        service.apply(plan)
    service.apply(plan, authorized=True)
    assert not app.storage.blob_path("objects", record.sha256).exists()
    assert app.database().evidence_hashes() == []
    assert service.apply(service.plan(), authorized=True) == ()


def test_retention_protects_linked_and_unlinked_submissions(make_app: Factory) -> None:
    app = make_app()
    _, linked = create_run(app)
    pending = app.evidence().ingest(CONTENT + b"pending", role="target")
    app.settings = app.settings.model_copy(update={"retention": RetentionSettings(evidence_days=1)})
    plan = Housekeeping(app).plan(now=time.time() + 86400 * 1000)
    assert not any(c.data_class == "evidence" for c in plan.candidates)
    assert app.storage.verify_blob("objects", linked.sha256)
    assert app.storage.verify_blob("objects", pending.sha256)


@pytest.mark.parametrize("value", [0, -1, True, 0.5, "7"])
def test_evidence_retention_is_positive_strict(value: object) -> None:
    with pytest.raises(ValidationError):
        RetentionSettings(evidence_days=value)  # type: ignore[arg-type]


def test_cli_warnings_and_explicit_intent(make_app: Factory, app_root: Path, capsys) -> None:  # type: ignore[no-untyped-def]
    app = make_app()
    result, record = create_run(app)
    assert main(["delete", result.run_id, "--with-evidence"], root=app_root) == 0
    out, err = capsys.readouterr()
    assert "unshared originals deleted" in out
    for warning in ("replay", "reproducible", "Backups", "exported", "hash-only", "erasure"):
        assert warning in err
    assert not app.storage.blob_path("objects", record.sha256).exists()


def test_old_settings_remain_valid(app_root: Path) -> None:
    update_settings(app_root, retention={"evidence_days": None})
    assert main(["config", "--json"], root=app_root) == 0


def test_retention_uses_recorded_time_not_blob_mtime(make_app: Factory) -> None:
    import os

    app = make_app()
    result, record = create_run(app)
    DeletionService(app).delete(result.run_id)
    path = app.storage.blob_path("objects", record.sha256)
    future = time.time() + 86400 * 100
    os.utime(path, (future, future))
    app.database()._conn.execute("UPDATE evidence SET first_seen_at = '2000-01-01T00:00:00+00:00'")
    app.settings = app.settings.model_copy(update={"retention": RetentionSettings(evidence_days=1)})
    assert any(
        c.sha256 == record.sha256 and c.data_class == "evidence"
        for c in Housekeeping(app).plan().candidates
    )


def test_post_commit_audit_failure_keeps_blocking_journal(make_app: Factory) -> None:
    app = make_app()
    result, record = create_run(app)

    def fail_completion(event: Event) -> None:
        if event.data.get("outcome") == "complete":
            raise OSError("final receipt unavailable")

    app.bus.subscribe(fail_completion, EventType.DELETION_APPLIED, critical=True)
    with pytest.raises(EventDeliveryError, match="journal"):
        DeletionService(app).delete(result.run_id, with_evidence=True)
    assert app.database().get_run(result.run_id) is None
    assert not app.storage.blob_path("objects", record.sha256).exists()
    assert any(j.phase == "pending" for j in journals(app.storage.root))
    with pytest.raises(StorageError, match="uncertain deletion intent"):
        DeletionService(app).delete(result.run_id, with_evidence=True)


def test_original_hash_mismatch_is_not_deletion_permission(make_app: Factory) -> None:
    app = make_app()
    result, record = create_run(app)
    original = app.storage.blob_path("objects", record.sha256)
    original.chmod(0o600)
    original.write_bytes(b"corrupt synthetic data")
    with pytest.raises(StorageError, match="integrity mismatch"):
        DeletionService(app).delete(result.run_id, with_evidence=True)
    assert original.read_bytes() == b"corrupt synthetic data"
    assert app.database().get_run(result.run_id)


def test_empty_prior_report_directory_does_not_protect_unshared_artifacts(
    make_app: Factory,
) -> None:
    app = make_app()
    first, _ = create_run(app)
    second, _ = create_run(app, content=CONTENT + b"second independent run")
    empty = app.storage.path("reports", first.run_id)
    empty.mkdir()
    DeletionService(app).delete(first.run_id)
    assert empty.is_dir() and not list(empty.iterdir())
    manifest = app.storage.read_json("manifests", f"{second.run_id}.json")
    assert isinstance(manifest, dict)
    DeletionService(app).delete(second.run_id)
    assert all(
        not app.storage.blob_path("artifacts", ref["sha256"]).exists()
        for ref in manifest["artifacts"]
    )
