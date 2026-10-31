"""Spawn-importable workers: independent services, connections, handles and processes."""

import io
import json
import os
import time
from pathlib import Path
from typing import Any

from photoproof.contracts.analysis import AnalysisRequest
from photoproof.contracts.events import Event, EventType
from photoproof.core.bootstrap import bootstrap
from photoproof.core.controller import AnalysisController
from photoproof.core.database.store import Database
from photoproof.core.logging.audit import append_record
from photoproof.core.storage.locking import file_lock
from photoproof.core.storage.store import RuntimeStorage

INPUT = b"\xff\xd8\xff\xe0 synthetic concurrent evidence"
BLOB = b"identical artifact" * 8192


def analyze(root: str, ready: Any, go: Any, output: Any) -> None:
    app = bootstrap(Path(root), plugin_package="mockplugins.demo", environ={})
    try:
        with file_lock(app.storage.root, exclusive=False):
            app.start()
            ready.set()
            assert go.wait(30), "parent did not release analysis barrier"
            descriptor, record = app.ingestion().ingest(
                io.BytesIO(INPUT), declared_name="synthetic.jpg", role="target"
            )
            result = AnalysisController(app).analyze(
                AnalysisRequest(target=descriptor, use_cache=False)
            )
            app.database().link_submissions([record.submission_id], result.run_id)
            manifest = json.loads(Path(result.manifest_path).read_text())
            output.send(
                (result.run_id, result.status.value, [a["sha256"] for a in manifest["artifacts"]])
            )
    finally:
        app.stop()
        output.close()


def write_many(root: str, go: Any, output: Any, index: int) -> None:
    storage = RuntimeStorage(Path(root))
    db = Database.open(storage.path("database", "photoproof.db"))
    try:
        assert go.wait(30)
        with file_lock(storage.root, exclusive=False):
            for number in range(15):
                digest = storage.put_blob("artifacts", BLOB)
                run_id = f"writer-{index}-{number}"
                db.create_run(run_id, mode="single", profile="standard", cache_key=None)
                db.finish_run(run_id, status="NO_DETECTORS", cache_hit=False, manifest_sha256=None)
                append_record(
                    storage.path("logs"), {"writer": index, "n": number, "data": "Ω" * 8192}
                )
        output.send(digest)
    finally:
        db.close()
        output.close()


def active(root: str, ready: Any, finish: Any, output: Any) -> None:
    app = bootstrap(Path(root), plugin_package="mockplugins.demo", environ={})
    try:
        with file_lock(app.storage.root, exclusive=False):
            app.start()
            db = app.database()

            def pause(event: Event) -> None:
                db._conn.execute("UPDATE runs SET created_at = '2000-01-01T00:00:00+00:00'")
                with app.storage.temp_file() as temporary:
                    temporary.write_bytes(INPUT)
                    old = time.time() - 72 * 3600
                    os.utime(temporary, (old, old))
                    output.send((str(temporary), event.run_id))
                    ready.set()
                    with temporary.open("rb") as live_handle:
                        assert finish.wait(30), "parent did not release active worker"
                        assert live_handle.read() == INPUT
                    assert temporary.read_bytes() == INPUT

            app.bus.subscribe(
                pause, EventType.RUN_STARTED, critical=True, name="pause-test-analysis"
            )
            descriptor, _ = app.ingestion().ingest(
                io.BytesIO(INPUT), declared_name="synthetic.jpg", role="target"
            )
            AnalysisController(app).analyze(AnalysisRequest(target=descriptor, use_cache=False))
    finally:
        app.stop()
        output.close()


def crash_lock(root: str, ready: Any, *, exclusive: bool) -> None:
    with file_lock(Path(root), exclusive=exclusive):
        ready.set()
        time.sleep(30)  # parent terminates this process; no Python cleanup runs


def database_lock(path: str, ready: Any, finish: Any) -> None:
    db = Database.open(Path(path))
    try:
        with db.transaction():
            ready.set()
            assert finish.wait(30)
    finally:
        db.close()


def initialize_database(path: str, barrier: Any, output: Any, *, migrate: bool) -> None:
    barrier.wait(20)
    db = Database.open(Path(path), allow_migration=migrate)
    try:
        output.send((db.schema_version(), db.integrity_problems()))
    finally:
        db.close()
        output.close()
