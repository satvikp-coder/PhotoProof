import socket
import sqlite3
from pathlib import Path

import pytest

from photoproof.contracts.exceptions import DatabaseError
from photoproof.core.database import store
from photoproof.core.database.store import LATEST_VERSION, Database
from photoproof.core.storage.processes import process_token


def test_new_database_is_migrated(tmp_path: Path) -> None:
    db = Database.open(tmp_path / "x.db")
    assert db.schema_version() == LATEST_VERSION and db.integrity_problems() == []
    db.create_run("r1", mode="single", profile="standard", cache_key=None)
    db.record_plugin_state("r1", "p.a", state="RUNNING", started_at="t0")
    db.record_plugin_state("r1", "p.a", state="SUCCESS", finished_at="t1")
    db.finish_run("r1", status="COMPLETED", cache_hit=False, manifest_sha256="f" * 64)
    assert db.plugin_states("r1") == {"p.a": "SUCCESS"}
    assert db.get_run("r1")["status"] == "COMPLETED"  # type: ignore[index]
    assert [r["run_id"] for r in db.recent_runs()] == ["r1"]
    db.close()


def test_transaction_rolls_back(tmp_path: Path) -> None:
    db = Database.open(tmp_path / "x.db")
    with pytest.raises(RuntimeError), db.transaction() as conn:
        conn.execute(
            "INSERT INTO runs (run_id, created_at, status, mode, profile) "
            "VALUES ('r1', 'now', 'X', 'single', 'p')"
        )
        raise RuntimeError("fail mid-transaction")
    assert db.get_run("r1") is None
    db.close()


def test_write_failure_is_a_database_error(tmp_path: Path) -> None:
    db = Database.open(tmp_path / "x.db")
    with pytest.raises(DatabaseError, match="write failed"):
        db.record_plugin_state("no-such-run", "p.a", state="RUNNING")  # foreign key
    db.close()


def test_newer_schema_requires_manual_action(tmp_path: Path) -> None:
    path = tmp_path / "x.db"
    conn = sqlite3.connect(path)
    conn.execute(f"PRAGMA user_version = {LATEST_VERSION + 5}")
    conn.close()
    with pytest.raises(DatabaseError, match="newer than this release"):
        Database.open(path)


def test_pending_migration_needs_controlled_repair(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "x.db"
    Database.open(path).close()
    monkeypatch.setattr(store, "MIGRATIONS", (*store.MIGRATIONS, "CREATE TABLE extra (x INTEGER)"))
    monkeypatch.setattr(store, "LATEST_VERSION", LATEST_VERSION + 1)
    with pytest.raises(DatabaseError, match="needs migration"):
        Database.open(path)
    db = Database.open(path, allow_migration=True)
    assert db.schema_version() == LATEST_VERSION + 1
    assert (tmp_path / f"x.db.v{LATEST_VERSION}.bak").is_file()  # backup taken first
    db.close()


def test_failed_migration_rolls_back(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    path = tmp_path / "x.db"
    Database.open(path).close()
    monkeypatch.setattr(store, "MIGRATIONS", (*store.MIGRATIONS, "CREATE TABLE ok (x); NOT SQL"))
    monkeypatch.setattr(store, "LATEST_VERSION", LATEST_VERSION + 1)
    with pytest.raises(DatabaseError, match="rolled back"):
        Database.open(path, allow_migration=True)
    conn = sqlite3.connect(path)
    assert conn.execute("PRAGMA user_version").fetchone()[0] == LATEST_VERSION
    assert conn.execute("SELECT name FROM sqlite_master WHERE name='ok'").fetchone() is None
    conn.close()


def test_self_process_inspection_failure_records_unknown_token(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Run creation must not fail when self-process inspection is unavailable.

    When `process_token(os.getpid())` raises `ProcessLookupError` (e.g. restricted
    procfs/PID visibility), `create_run` must persist NULL as the token rather than
    propagating the exception. Stale-run recovery stays conservative: unknown owners
    are never automatically recovered.
    """
    path = tmp_path / "x.db"
    db = Database.open(path)

    # Mock process_token to raise ProcessLookupError for our own PID.
    real_pid = os.getpid()

    def fake_process_token(pid: int) -> str | None:
        if pid == real_pid:
            raise ProcessLookupError(pid)
        return process_token(pid)

    monkeypatch.setattr(store_mod, "process_token", fake_process_token)

    # Run creation must succeed despite self-inspection failure.
    db.create_run("r1", mode="single", profile="standard", cache_key=None)
    row = db.get_run("r1")
    assert row["pid"] == real_pid
    assert row["hostname"] == socket.gethostname()
    assert row["process_token"] is None

    # owner_state stays conservative: unknown token means "unknown", not "dead".
    from photoproof.core.storage.processes import owner_state

    assert owner_state(row) == "unknown"

    # A dead-owner recovery must not abort this run.
    db.mark_runs_aborted(["r1"])
    assert db.get_run("r1")["finished_at"] is None

    db.close()
