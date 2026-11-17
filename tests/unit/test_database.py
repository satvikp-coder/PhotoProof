import sqlite3
from pathlib import Path

import pytest

from photoproof.contracts.exceptions import DatabaseError
from photoproof.core.database import store
from photoproof.core.database.store import LATEST_VERSION, Database, open_connection_count


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
    try:
        conn.execute(f"PRAGMA user_version = {LATEST_VERSION + 5}")
    finally:
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
    try:
        assert conn.execute("PRAGMA user_version").fetchone()[0] == LATEST_VERSION
        assert conn.execute("SELECT name FROM sqlite_master WHERE name='ok'").fetchone() is None
    finally:
        conn.close()


# ------------------------------------------------------------------ connection lifecycle


def test_no_connection_left_open_after_normal_use(tmp_path: Path) -> None:
    """Opening and closing a database leaves no connection open."""
    assert open_connection_count() == 0
    path = tmp_path / "normal.db"
    db = Database.open(path)
    assert open_connection_count() == 1
    db.create_run("r1", mode="single", profile="standard", cache_key=None)
    db.close()
    assert open_connection_count() == 0


def test_no_connection_left_open_after_startup_failure(tmp_path: Path) -> None:
    """A failed open leaves no connection open (every error path closes)."""
    assert open_connection_count() == 0
    path = tmp_path / "fail.db"
    # Newer schema than this release: open must fail and close the connection.
    conn = sqlite3.connect(path)
    conn.execute(f"PRAGMA user_version = {LATEST_VERSION + 5}")
    conn.close()
    with pytest.raises(DatabaseError, match="newer than this release"):
        Database.open(path)
    assert open_connection_count() == 0


def test_context_manager_closes_connection(tmp_path: Path) -> None:
    """Using Database as a context manager closes the connection on exit."""
    assert open_connection_count() == 0
    path = tmp_path / "ctx.db"
    with Database.open(path) as db:
        assert open_connection_count() == 1
        db.create_run("r1", mode="single", profile="standard", cache_key=None)
    assert open_connection_count() == 0
    assert db._closed


def test_no_connection_left_open_after_app_shutdown(make_app) -> None:
    """stop() closes the application's database connection."""
    assert open_connection_count() == 0
    app = make_app()
    app.database().create_run("r1", mode="single", profile="standard", cache_key=None)
    assert open_connection_count() == 1
    app.stop()
    assert open_connection_count() == 0
