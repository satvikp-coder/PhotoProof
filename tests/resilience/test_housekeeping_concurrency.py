"""Real local-process audit/SQLite coordination; all waits are bounded."""

import json
import multiprocessing
import sqlite3
from contextlib import closing
from pathlib import Path

from photoproof.core.logging.audit import append_audit


def _writer(path: str, worker: int) -> None:
    for index in range(20):
        append_audit(Path(path), {"worker": worker, "index": index}, max_bytes=200)


def _reservation(path: str, ready, release) -> None:  # type: ignore[no-untyped-def]
    connection = sqlite3.connect(path, timeout=2)
    try:
        connection.execute("BEGIN EXCLUSIVE")
        ready.set()
        if not release.wait(10):
            raise RuntimeError("test reservation release timed out")
    finally:
        connection.close()


def test_audit_rotation_with_independent_writers(tmp_path: Path) -> None:
    context = multiprocessing.get_context("spawn")
    path = tmp_path / "audit.jsonl"
    children = [context.Process(target=_writer, args=(str(path), index)) for index in range(3)]
    try:
        for child in children:
            child.start()
        for child in children:
            child.join(20)
            assert not child.is_alive() and child.exitcode == 0
    finally:
        for child in children:
            if child.is_alive():
                child.terminate()
                child.join(5)
    segments = [*tmp_path.glob("audit.jsonl.*"), path]
    records = [json.loads(line) for part in segments for line in part.read_bytes().splitlines()]
    assert len(records) == 60
    assert {(r["worker"], r["index"]) for r in records} == {
        (worker, index) for worker in range(3) for index in range(20)
    }


def test_exclusive_maintenance_reservation_blocks_other_process(tmp_path: Path) -> None:
    context = multiprocessing.get_context("spawn")
    path = tmp_path / "runtime.db"
    with closing(sqlite3.connect(path)) as connection:
        connection.execute("CREATE TABLE sample (value TEXT)")
    ready, release = context.Event(), context.Event()
    child = context.Process(target=_reservation, args=(str(path), ready, release))
    child.start()
    try:
        assert ready.wait(10)
        with closing(sqlite3.connect(path, timeout=0.05)) as connection:
            try:
                connection.execute("BEGIN IMMEDIATE")
            except sqlite3.OperationalError as error:
                assert "locked" in str(error)
            else:
                raise AssertionError("maintenance reservation failed to exclude a producer")
        release.set()
        child.join(10)
        assert child.exitcode == 0
        with closing(sqlite3.connect(path, timeout=1)) as connection:
            connection.execute("INSERT INTO sample VALUES ('after release')")
            connection.commit()
    finally:
        release.set()
        if child.is_alive():
            child.terminate()
        child.join(5)
