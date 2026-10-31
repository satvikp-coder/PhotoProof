"""Real spawn processes sharing a disposable runtime, including native Windows locks."""

import hashlib
import json
import multiprocessing
import os
import socket
import sqlite3
import threading
import time
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

import concurrency_workers as workers
import pytest

from photoproof.contracts.enums import Severity
from photoproof.contracts.exceptions import StorageError
from photoproof.core.bootstrap import bootstrap
from photoproof.core.database.store import BUSY_TIMEOUT_MS, LATEST_VERSION, MIGRATIONS, Database
from photoproof.core.health.checks import (
    HealthRegistry,
    check_audit,
    check_stale_runs,
    check_stale_temp,
)
from photoproof.core.health.repair import run_repairs
from photoproof.core.logging.audit import append_record, audit_problems
from photoproof.core.storage.locking import file_lock
from photoproof.core.storage.processes import owner_state, process_token
from photoproof.core.storage.store import sha256_file

CTX = multiprocessing.get_context("spawn")


@contextmanager
def children(*processes: Any) -> Iterator[None]:
    try:
        for process in processes:
            process.start()
        yield
        for process in processes:
            process.join(30)
            assert not process.is_alive(), "child exceeded bounded join"
            assert process.exitcode == 0
    finally:
        for process in processes:
            if process.is_alive():
                process.terminate()
                process.join(10)
            process.close()


def initialized(root: Path) -> Path:
    app = bootstrap(root, plugin_package="mockplugins.demo", environ={})
    app.storage.ensure_layout()
    app.database().close()
    return app.storage.root


def registry() -> HealthRegistry:
    checks = HealthRegistry()
    checks.register("database.stale_runs", check_stale_runs)
    checks.register("runtime.stale_temp", check_stale_temp)
    return checks


def test_two_independent_analyses_complete(app_root: Path) -> None:
    runtime = initialized(app_root)
    go = CTX.Event()
    ready = [CTX.Event(), CTX.Event()]
    pipes = [CTX.Pipe(duplex=False) for _ in range(2)]
    processes = [
        CTX.Process(target=workers.analyze, args=(str(app_root), ready[i], go, pipes[i][1]))
        for i in range(2)
    ]
    with children(*processes):
        # Both must hold shared locks simultaneously; serial execution cannot pass this barrier.
        assert all(event.wait(20) for event in ready)
        go.set()
        results = []
        for read, write in pipes:
            assert read.poll(30)
            results.append(read.recv())
            read.close()
            write.close()
    assert len({row[0] for row in results}) == 2
    assert [row[1] for row in results] == ["COMPLETED", "COMPLETED"]
    assert results[0][2] == results[1][2] and results[0][2]
    db = Database.open(runtime / "database" / "photoproof.db")
    try:
        assert db.integrity_problems() == []
        for run_id, _, hashes in results:
            manifest = runtime / "manifests" / f"{run_id}.json"
            assert sha256_file(manifest) == db.get_run(run_id)["manifest_sha256"]
            for digest in hashes:
                assert sha256_file(runtime / "artifacts" / digest[:2] / digest) == digest
    finally:
        db.close()


def test_concurrent_blobs_sqlite_and_large_audit_records(app_root: Path) -> None:
    runtime = initialized(app_root)
    go = CTX.Event()
    pipes = [CTX.Pipe(duplex=False) for _ in range(3)]
    processes = [
        CTX.Process(target=workers.write_many, args=(str(runtime), go, pipes[i][1], i))
        for i in range(3)
    ]
    with children(*processes):
        go.set()
        for read, write in pipes:
            assert read.poll(30)
            assert read.recv() == hashlib.sha256(workers.BLOB).hexdigest()
            read.close()
            write.close()
    rows = [
        json.loads(line) for line in (runtime / "logs" / "audit.jsonl").read_bytes().splitlines()
    ]
    assert {(r["writer"], r["n"]) for r in rows} == {(i, n) for i in range(3) for n in range(15)}
    assert len(rows) == 45 and all(r["data"] == "Ω" * 8192 for r in rows)
    assert audit_problems(runtime / "logs") == []
    digest = hashlib.sha256(workers.BLOB).hexdigest()
    blob = runtime / "artifacts" / digest[:2] / digest
    assert blob.read_bytes() == workers.BLOB and sha256_file(blob) == digest
    db = Database.open(runtime / "database" / "photoproof.db")
    try:
        assert len(db.recent_runs(100)) == 45 and db.integrity_problems() == []
    finally:
        db.close()


def test_live_run_and_temp_survive_repair_then_dead_owner_is_recovered(app_root: Path) -> None:
    initialized(app_root)
    ready, finish = CTX.Event(), CTX.Event()
    read, write = CTX.Pipe(duplex=False)
    process = CTX.Process(target=workers.active, args=(str(app_root), ready, finish, write))
    app = bootstrap(app_root, plugin_package="mockplugins.demo", environ={})
    try:
        process.start()
        assert ready.wait(20) and read.poll(5)
        temporary_name, run_id = read.recv()
        temporary = Path(temporary_name)
        assert check_stale_runs(app).severity is Severity.OK
        assert check_stale_temp(app).severity is Severity.WARNING
        with pytest.raises(StorageError, match="timed out"):
            run_repairs(app, registry(), allow_controlled=True, dry_run=False, lock_timeout=0.2)
        assert temporary.read_bytes() == workers.INPUT
        assert app.database().get_run(run_id)["finished_at"] is None
        # Hard termination skips finally blocks, leaving old metadata and a staging file.
        process.terminate()
        process.join(10)
        assert not process.is_alive()
        assert check_stale_runs(app).severity is Severity.WARNING
        records = run_repairs(app, registry(), allow_controlled=True, dry_run=False)
        assert len(records) == 2 and all(r.verified for r in records)
        assert not temporary.exists()
        assert app.database().get_run(run_id)["status"] == "ABORTED"
        assert run_repairs(app, registry(), allow_controlled=True, dry_run=False) == []
    finally:
        if process.is_alive():
            process.terminate()
            process.join(10)
        process.close()
        read.close()
        write.close()
        app.stop()


def test_repair_waits_and_rechecks_after_live_analysis_finishes(app_root: Path) -> None:
    initialized(app_root)
    ready, finish = CTX.Event(), CTX.Event()
    read, write = CTX.Pipe(duplex=False)
    process = CTX.Process(target=workers.active, args=(str(app_root), ready, finish, write))
    app = bootstrap(app_root, plugin_package="mockplugins.demo", environ={})
    try:
        with children(process):
            assert ready.wait(20) and read.poll(5)
            temporary_name, run_id = read.recv()
            snapshot = registry().run_all(app)
            assert any(r.check_id == "runtime.stale_temp" and not r.ok for r in snapshot)
            timer = threading.Timer(0.3, finish.set)
            timer.start()
            try:
                records = run_repairs(
                    app,
                    registry(),
                    allow_controlled=True,
                    dry_run=False,
                    results=snapshot,
                    lock_timeout=10,
                )
            finally:
                finish.set()
                timer.join(5)
        assert records == []  # stale snapshot was discarded under the exclusive lock
        assert not Path(temporary_name).exists()  # the writer performed its own cleanup
        assert app.database().get_run(run_id)["status"] == "COMPLETED"
    finally:
        finish.set()
        read.close()
        write.close()
        app.stop()


@pytest.mark.parametrize("exclusive", [True, False])
def test_os_releases_locks_after_abnormal_exit(tmp_path: Path, exclusive: bool) -> None:
    ready = CTX.Event()
    process = CTX.Process(
        target=workers.crash_lock, args=(str(tmp_path), ready), kwargs={"exclusive": exclusive}
    )
    try:
        process.start()
        assert ready.wait(20)
        with (
            pytest.raises(StorageError, match="never remove"),
            file_lock(tmp_path, exclusive=True, timeout=0.1),
        ):
            pytest.fail("conflicting acquisition succeeded")
        if exclusive:
            with (
                pytest.raises(StorageError, match="timed out"),
                file_lock(tmp_path, exclusive=False, timeout=0.1),
            ):
                pytest.fail("analysis entered while repair owned the lock")
        process.terminate()
        process.join(10)
        assert not process.is_alive()
        # A stale file is harmless. Do not unlink it or infer ownership from its bytes.
        assert (tmp_path / ".runtime.lock").exists()
        with file_lock(tmp_path, exclusive=True, timeout=1):
            pass
    finally:
        if process.is_alive():
            process.terminate()
            process.join(10)
        process.close()


def test_sqlite_waits_for_other_process_writer(app_root: Path) -> None:
    runtime = initialized(app_root)
    path = runtime / "database" / "photoproof.db"
    ready, finish = CTX.Event(), CTX.Event()
    process = CTX.Process(target=workers.database_lock, args=(str(path), ready, finish))
    db = Database.open(path)
    try:
        with children(process):
            assert ready.wait(20)
            timer = threading.Timer(0.3, finish.set)
            timer.start()
            try:
                started = time.monotonic()
                db.create_run("waited", mode="single", profile="standard", cache_key=None)
                assert time.monotonic() - started >= 0.2
            finally:
                finish.set()
                timer.join(5)
        assert db._conn.execute("PRAGMA busy_timeout").fetchone()[0] == BUSY_TIMEOUT_MS
        assert db.integrity_problems() == []
    finally:
        finish.set()
        db.close()


def test_legacy_migration_and_ambiguous_processes_are_not_aborted(tmp_path: Path) -> None:
    path = tmp_path / "legacy.db"
    conn = sqlite3.connect(path)
    conn.executescript(MIGRATIONS[0] + MIGRATIONS[1])
    conn.execute("PRAGMA user_version = 2")
    conn.execute(
        "INSERT INTO runs (run_id, created_at, status, mode, profile) "
        "VALUES ('legacy', '2000', 'RUNNING', 'single', 'p')"
    )
    conn.commit()
    conn.close()
    db = Database.open(path, allow_migration=True)
    try:
        assert (tmp_path / "legacy.db.v2.bak").is_file()
        row = db.get_run("legacy")
        assert row["pid"] is None and row["hostname"] is None and owner_state(row) == "unknown"
        db.mark_runs_aborted(["legacy"])
        assert db.get_run("legacy")["finished_at"] is None
        local = {
            "pid": os.getpid(),
            "hostname": socket.gethostname(),
            "process_token": process_token(os.getpid()),
        }
        assert owner_state(local) == "alive"
        assert owner_state({**local, "hostname": "another-host"}) == "unknown"
        assert owner_state({**local, "process_token": "previous-incarnation"}) == "dead"
    finally:
        db.close()


@pytest.mark.parametrize("legacy", [False, True])
def test_concurrent_schema_initialization_or_migration(tmp_path: Path, legacy: bool) -> None:
    path = tmp_path / "shared.db"
    if legacy:
        conn = sqlite3.connect(path)
        conn.executescript(MIGRATIONS[0] + MIGRATIONS[1])
        conn.execute("PRAGMA user_version = 2")
        conn.close()
    barrier = CTX.Barrier(2)
    pipes = [CTX.Pipe(duplex=False) for _ in range(2)]
    processes = [
        CTX.Process(
            target=workers.initialize_database,
            args=(str(path), barrier, pipes[i][1]),
            kwargs={"migrate": legacy},
        )
        for i in range(2)
    ]
    with children(*processes):
        for read, write in pipes:
            assert read.poll(30)
            assert read.recv() == (LATEST_VERSION, [])
            read.close()
            write.close()
    if legacy:
        backup = sqlite3.connect(tmp_path / "shared.db.v2.bak")
        try:
            assert backup.execute("PRAGMA user_version").fetchone()[0] == 2
        finally:
            backup.close()


def test_audit_crash_fragment_is_reported_and_preserved(app_root: Path) -> None:
    runtime = initialized(app_root)
    path = runtime / "logs" / "audit.jsonl"
    fragment = b'{"unfinished":'
    path.write_bytes(fragment)
    with pytest.raises(StorageError, match="incomplete audit record"):
        append_record(path.parent, {"next": "record"})
    assert path.read_bytes() == fragment
    app = bootstrap(app_root, plugin_package="mockplugins.demo", environ={})
    try:
        assert check_audit(app).severity is Severity.ERROR
    finally:
        app.stop()


def test_lock_reentrancy_exceptions_and_hardlink_refusal(tmp_path: Path) -> None:
    with (
        pytest.raises(RuntimeError),
        file_lock(tmp_path, exclusive=True),
        file_lock(tmp_path, exclusive=False),
    ):
        raise RuntimeError("interrupt work")
    with (
        file_lock(tmp_path, exclusive=False),
        pytest.raises(StorageError, match="upgrade"),
        file_lock(tmp_path, exclusive=True, timeout=0),
    ):
        pytest.fail("upgrade succeeded")
    lock_path = tmp_path / ".runtime.lock"
    lock_path.write_text("stale metadata is not ownership")
    with file_lock(tmp_path, exclusive=True):
        pass  # Windows byte-range locks also deny reads through another handle.
    assert lock_path.read_text() == "stale metadata is not ownership"
    alias = tmp_path / ".alias"
    os.link(tmp_path / ".runtime.lock", alias)
    with pytest.raises(StorageError, match="unsafe"), file_lock(tmp_path, exclusive=True):
        pytest.fail("hardlinked lock accepted")


def test_remote_run_is_a_manual_finding_not_aborted(app_root: Path) -> None:
    initialized(app_root)
    app = bootstrap(app_root, plugin_package="mockplugins.demo", environ={})
    try:
        db = app.database()
        db.create_run("remote", mode="single", profile="standard", cache_key=None)
        db._conn.execute("UPDATE runs SET created_at = '2000', hostname = 'another-host'")
        result = check_stale_runs(app)
        assert result.severity is Severity.WARNING
        assert result.repair is not None and result.repair.apply is None
        db.mark_runs_aborted(["remote"])
        assert db.get_run("remote")["finished_at"] is None
    finally:
        app.stop()
