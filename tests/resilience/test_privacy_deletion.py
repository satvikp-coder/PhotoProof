"""Real-process publication reservation closes the upload/deletion race."""

import multiprocessing
import os
import threading
from collections.abc import Callable
from pathlib import Path

import pytest

from photoproof.contracts.analysis import AnalysisRequest, InputDescriptor
from photoproof.contracts.events import Event, EventType
from photoproof.contracts.exceptions import StorageError
from photoproof.core.bootstrap import Application, bootstrap
from photoproof.core.controller import AnalysisController
from photoproof.core.services.deletion import DeletionService

CONTENT = b"\xff\xd8\xff concurrent synthetic input"


def _upload(root: str, ready, release) -> None:  # type: ignore[no-untyped-def]
    app = bootstrap(Path(root), environ={})
    try:
        evidence = app.evidence()
        store = evidence._store

        def pause(reader, label):  # type: ignore[no-untyped-def]
            result = store(reader, label)
            ready.set()
            if not release.wait(10):
                raise RuntimeError("upload test release timed out")
            return result

        evidence._store = pause  # type: ignore[method-assign]
        evidence.ingest(CONTENT, role="target", original_name="other person.jpg")
    finally:
        app.stop()


def test_upload_reservation_serializes_and_protects_new_submission(
    make_app: Callable[..., Application], app_root: Path
) -> None:
    app = make_app()
    record = app.evidence().ingest(CONTENT, role="target")
    result = AnalysisController(app).analyze(
        AnalysisRequest(
            target=InputDescriptor(role="target", sha256=record.sha256, size=record.size),
            use_cache=False,
        )
    )
    app.database().link_submissions([record.submission_id], result.run_id)
    context = multiprocessing.get_context("spawn")
    ready, release = context.Event(), context.Event()
    child = context.Process(target=_upload, args=(str(app_root), ready, release))
    child.start()
    timer = threading.Timer(0.3, release.set)
    try:
        assert ready.wait(10)
        timer.start()
        with pytest.raises(StorageError, match="submissions"):
            DeletionService(app).delete(result.run_id, with_evidence=True)
        child.join(15)
        assert child.exitcode == 0
        assert len(app.database().submissions(record.sha256)) == 2
        assert app.storage.verify_blob("objects", record.sha256)
        assert app.database().get_run(result.run_id)
    finally:
        timer.cancel()
        release.set()
        if child.is_alive():
            child.terminate()
        child.join(5)


def _crash_after_intent(root: str, run_id: str) -> None:
    app = bootstrap(Path(root), environ={})

    def crash(event: Event) -> None:
        if event.data.get("outcome") == "intent":
            os._exit(23)  # disposable child only; simulates loss after durable intent

    app.bus.subscribe(crash, EventType.DELETION_APPLIED, critical=True)
    DeletionService(app).delete(run_id, with_evidence=True)


def test_abnormal_exit_after_intent_blocks_continuation(
    make_app: Callable[..., Application], app_root: Path
) -> None:
    from photoproof.core.services.housekeeping import Housekeeping
    from photoproof.core.storage.deletion_journal import journals

    app = make_app()
    record = app.evidence().ingest(CONTENT, role="target")
    result = AnalysisController(app).analyze(
        AnalysisRequest(
            target=InputDescriptor(role="target", sha256=record.sha256, size=record.size),
            use_cache=False,
        )
    )
    app.database().link_submissions([record.submission_id], result.run_id)
    child = multiprocessing.get_context("spawn").Process(
        target=_crash_after_intent, args=(str(app_root), result.run_id)
    )
    child.start()
    try:
        child.join(15)
        assert child.exitcode == 23
        assert app.storage.verify_blob("objects", record.sha256)
        assert any(j.phase == "pending" for j in journals(app.storage.root))
        with pytest.raises(StorageError, match="uncertain deletion intent"):
            DeletionService(app).delete(result.run_id, with_evidence=True)
        assert Housekeeping(app).plan().blocked
    finally:
        if child.is_alive():
            child.terminate()
        child.join(5)
