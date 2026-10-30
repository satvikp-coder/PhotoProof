"""SQLite persistence layer with versioned migrations.

The schema version is ``PRAGMA user_version``. Migrations run in a single transaction
each; an existing database is backed up with the SQLite backup API before any
migration is applied to it.
"""

import sqlite3
from collections.abc import Iterator, Sequence
from contextlib import contextmanager, suppress
from datetime import UTC, datetime
from pathlib import Path

from photoproof.contracts.exceptions import DatabaseError, MigrationRequiredError

MIGRATIONS: Sequence[str] = (
    # 1: initial schema
    """
    CREATE TABLE runs (
        run_id TEXT PRIMARY KEY,
        created_at TEXT NOT NULL,
        finished_at TEXT,
        status TEXT NOT NULL,
        mode TEXT NOT NULL,
        profile TEXT NOT NULL,
        cache_key TEXT,
        cache_hit INTEGER NOT NULL DEFAULT 0,
        manifest_sha256 TEXT
    );
    CREATE TABLE plugin_executions (
        run_id TEXT NOT NULL REFERENCES runs(run_id),
        plugin_id TEXT NOT NULL,
        state TEXT NOT NULL,
        failure_category TEXT,
        message TEXT,
        started_at TEXT,
        finished_at TEXT,
        PRIMARY KEY (run_id, plugin_id)
    );
    CREATE TABLE artifacts (
        sha256 TEXT NOT NULL,
        run_id TEXT NOT NULL REFERENCES runs(run_id),
        plugin_id TEXT NOT NULL,
        name TEXT NOT NULL,
        media_type TEXT NOT NULL,
        size INTEGER NOT NULL,
        PRIMARY KEY (run_id, plugin_id, name)
    );
    CREATE TABLE cache_entries (
        cache_key TEXT PRIMARY KEY,
        run_id TEXT NOT NULL,
        created_at TEXT NOT NULL
    );
    CREATE TABLE health_checks (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        checked_at TEXT NOT NULL,
        check_id TEXT NOT NULL,
        severity TEXT NOT NULL,
        message TEXT NOT NULL
    );
    """,
    # 2: original evidence and who submitted it
    """
    CREATE TABLE evidence (
        sha256 TEXT PRIMARY KEY CHECK (length(sha256) = 64 AND sha256 NOT GLOB '*[^0-9a-f]*'),
        size INTEGER NOT NULL,
        first_seen_at TEXT NOT NULL
    );
    CREATE TABLE submissions (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        sha256 TEXT NOT NULL REFERENCES evidence(sha256),
        role TEXT NOT NULL,
        original_name TEXT,
        submitted_at TEXT NOT NULL,
        run_id TEXT REFERENCES runs(run_id)
    );
    CREATE INDEX submissions_sha256 ON submissions(sha256);
    """,
)
LATEST_VERSION = len(MIGRATIONS)


def _now() -> str:
    return datetime.now(UTC).isoformat()


class Database:
    def __init__(self, connection: sqlite3.Connection, path: Path) -> None:
        self._conn = connection
        self.path = path

    # --------------------------------------------------------------- lifecycle

    @classmethod
    def open(cls, path: Path, *, allow_migration: bool = False) -> "Database":
        """Open and, when safe, migrate the database.

        A new (empty) database is always initialized. Migrating an existing database
        requires ``allow_migration`` (the CONTROLLED ``--repair`` path).
        """
        path.parent.mkdir(parents=True, exist_ok=True)
        try:
            conn = sqlite3.connect(path, isolation_level=None)  # explicit transactions
            conn.execute("PRAGMA foreign_keys = ON")
            conn.row_factory = sqlite3.Row
        except sqlite3.Error as error:
            raise DatabaseError(f"{path}: cannot open database: {error}") from error
        db = cls(conn, path)
        try:
            version = db.schema_version()
        except sqlite3.DatabaseError as error:  # e.g. the file is not a SQLite database
            conn.close()
            raise DatabaseError(f"{path}: unreadable database: {error}") from error
        if version > LATEST_VERSION:
            conn.close()
            raise DatabaseError(
                f"{path}: schema version {version} is newer than this release supports "
                f"({LATEST_VERSION}). Manual action required: upgrade PhotoProof."
            )
        if 0 < version < LATEST_VERSION and not allow_migration:
            conn.close()
            raise MigrationRequiredError(
                f"{path}: schema version {version} needs migration to {LATEST_VERSION}. "
                "Run `python main.py --repair` (a backup is taken first)."
            )
        if version < LATEST_VERSION:
            try:
                db._migrate(version)
            except DatabaseError:
                conn.close()
                raise
        return db

    def close(self) -> None:
        self._conn.close()

    def schema_version(self) -> int:
        return int(self._conn.execute("PRAGMA user_version").fetchone()[0])

    def _migrate(self, current: int) -> None:
        if current > 0:
            self.backup(self.path.with_name(f"{self.path.name}.v{current}.bak"))
        for version in range(current + 1, LATEST_VERSION + 1):
            try:
                self._conn.execute("BEGIN IMMEDIATE")
                # ponytail: naive ";" split — migrations must not contain ";" inside
                # string literals or triggers; switch to a statement parser if they do.
                for statement in MIGRATIONS[version - 1].split(";"):
                    if statement.strip():
                        self._conn.execute(statement)
                self._conn.execute(f"PRAGMA user_version = {version}")
                self._conn.execute("COMMIT")
            except sqlite3.Error as error:
                with suppress(sqlite3.Error):  # never mask the original failure
                    self._conn.execute("ROLLBACK")
                raise DatabaseError(
                    f"{self.path}: migration to version {version} failed and was rolled back: "
                    f"{error}. The database is unchanged; see `python main.py --doctor`."
                ) from error

    def backup(self, target: Path) -> Path:
        destination = sqlite3.connect(target)
        try:
            self._conn.backup(destination)
        finally:
            destination.close()
        return target

    @contextmanager
    def transaction(self) -> Iterator[sqlite3.Connection]:
        """Run statements atomically; any exception rolls everything back."""
        self._conn.execute("BEGIN")
        try:
            yield self._conn
        except BaseException:
            with suppress(sqlite3.Error):  # never mask the original failure
                self._conn.execute("ROLLBACK")
            raise
        self._conn.execute("COMMIT")

    def integrity_problems(self) -> list[str]:
        rows = self._conn.execute("PRAGMA integrity_check").fetchall()
        problems = [str(row[0]) for row in rows if row[0] != "ok"]
        problems += [
            f"foreign key violation in {row[0]}"
            for row in self._conn.execute("PRAGMA foreign_key_check").fetchall()
        ]
        return problems

    # ------------------------------------------------------------------ writes

    def _write(self, sql: str, params: Sequence[object]) -> None:
        try:
            with self.transaction() as conn:
                conn.execute(sql, params)
        except sqlite3.Error as error:
            raise DatabaseError(f"{self.path}: write failed: {error}") from error

    def create_run(self, run_id: str, *, mode: str, profile: str, cache_key: str | None) -> None:
        self._write(
            "INSERT INTO runs (run_id, created_at, status, mode, profile, cache_key) "
            "VALUES (?, ?, 'CREATED', ?, ?, ?)",
            (run_id, _now(), mode, profile, cache_key),
        )

    def set_run_status(self, run_id: str, status: str) -> None:
        self._write("UPDATE runs SET status = ? WHERE run_id = ?", (status, run_id))

    def unfinished_runs(self, *, older_than: str) -> list[dict[str, object]]:
        """Runs without ``finished_at`` created before the ISO timestamp ``older_than``."""
        rows = self._conn.execute(
            "SELECT * FROM runs WHERE finished_at IS NULL AND created_at < ? ORDER BY created_at",
            (older_than,),
        ).fetchall()
        return [dict(row) for row in rows]

    def mark_runs_aborted(self, run_ids: Sequence[str]) -> None:
        try:
            with self.transaction() as conn:
                conn.executemany(
                    "UPDATE runs SET status = 'ABORTED', finished_at = ? "
                    "WHERE run_id = ? AND finished_at IS NULL",
                    [(_now(), run_id) for run_id in run_ids],
                )
        except sqlite3.Error as error:
            raise DatabaseError(f"{self.path}: write failed: {error}") from error

    def finish_run(
        self, run_id: str, *, status: str, cache_hit: bool, manifest_sha256: str | None
    ) -> None:
        self._write(
            "UPDATE runs SET status = ?, finished_at = ?, cache_hit = ?, manifest_sha256 = ? "
            "WHERE run_id = ?",
            (status, _now(), int(cache_hit), manifest_sha256, run_id),
        )

    def record_plugin_state(
        self,
        run_id: str,
        plugin_id: str,
        *,
        state: str,
        failure_category: str | None = None,
        message: str | None = None,
        started_at: str | None = None,
        finished_at: str | None = None,
    ) -> None:
        self._write(
            "INSERT INTO plugin_executions VALUES (?, ?, ?, ?, ?, ?, ?) "
            "ON CONFLICT (run_id, plugin_id) DO UPDATE SET state = excluded.state, "
            "failure_category = excluded.failure_category, message = excluded.message, "
            "started_at = COALESCE(excluded.started_at, started_at), "
            "finished_at = excluded.finished_at",
            (run_id, plugin_id, state, failure_category, message, started_at, finished_at),
        )

    def record_artifact(
        self, *, sha256: str, run_id: str, plugin_id: str, name: str, media_type: str, size: int
    ) -> None:
        self._write(
            "INSERT OR REPLACE INTO artifacts VALUES (?, ?, ?, ?, ?, ?)",
            (sha256, run_id, plugin_id, name, media_type, size),
        )

    def record_cache_entry(self, cache_key: str, run_id: str) -> None:
        self._write(
            "INSERT OR REPLACE INTO cache_entries VALUES (?, ?, ?)", (cache_key, run_id, _now())
        )

    def delete_cache_entry(self, cache_key: str) -> None:
        self._write("DELETE FROM cache_entries WHERE cache_key = ?", (cache_key,))

    def record_health_check(self, check_id: str, severity: str, message: str) -> None:
        self._write(
            "INSERT INTO health_checks (checked_at, check_id, severity, message) "
            "VALUES (?, ?, ?, ?)",
            (_now(), check_id, severity, message),
        )

    def record_submission(
        self, *, sha256: str, size: int, role: str, original_name: str | None
    ) -> dict[str, object]:
        """Record one submission of stored evidence, atomically with its first sighting.

        Returns ``submission_id``, ``first_seen_at`` and ``is_new`` (first time these bytes
        were seen).
        """
        now = _now()
        try:
            with self.transaction() as conn:
                inserted = conn.execute(
                    "INSERT OR IGNORE INTO evidence (sha256, size, first_seen_at) VALUES (?, ?, ?)",
                    (sha256, size, now),
                )
                submission = conn.execute(
                    "INSERT INTO submissions (sha256, role, original_name, submitted_at) "
                    "VALUES (?, ?, ?, ?)",
                    (sha256, role, original_name, now),
                )
                first_seen = conn.execute(
                    "SELECT first_seen_at FROM evidence WHERE sha256 = ?", (sha256,)
                ).fetchone()
                return {
                    "submission_id": submission.lastrowid,
                    "first_seen_at": first_seen[0],
                    "is_new": inserted.rowcount == 1,
                }
        except sqlite3.Error as error:
            raise DatabaseError(f"{self.path}: write failed: {error}") from error

    def link_submissions(self, submission_ids: Sequence[int], run_id: str) -> None:
        try:
            with self.transaction() as conn:
                conn.executemany(
                    "UPDATE submissions SET run_id = ? WHERE id = ?",
                    [(run_id, submission_id) for submission_id in submission_ids],
                )
        except sqlite3.Error as error:
            raise DatabaseError(f"{self.path}: write failed: {error}") from error

    # ------------------------------------------------------------------- reads

    def get_run(self, run_id: str) -> dict[str, object] | None:
        row = self._conn.execute("SELECT * FROM runs WHERE run_id = ?", (run_id,)).fetchone()
        return dict(row) if row else None

    def plugin_states(self, run_id: str) -> dict[str, str]:
        rows = self._conn.execute(
            "SELECT plugin_id, state FROM plugin_executions WHERE run_id = ? ORDER BY plugin_id",
            (run_id,),
        )
        return {row["plugin_id"]: row["state"] for row in rows}

    def recent_runs(self, limit: int = 20) -> list[dict[str, object]]:
        rows = self._conn.execute(
            "SELECT * FROM runs ORDER BY created_at DESC LIMIT ?", (limit,)
        ).fetchall()
        return [dict(row) for row in rows]

    def submissions(self, sha256: str) -> list[dict[str, object]]:
        rows = self._conn.execute(
            "SELECT * FROM submissions WHERE sha256 = ? ORDER BY id", (sha256,)
        ).fetchall()
        return [dict(row) for row in rows]

    def evidence_hashes(self) -> list[str]:
        try:
            rows = self._conn.execute("SELECT sha256 FROM evidence ORDER BY sha256").fetchall()
        except sqlite3.Error as error:
            raise DatabaseError(f"{self.path}: read failed: {error}") from error
        return [str(row[0]) for row in rows]
