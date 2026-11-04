"""Reference-aware maintenance: pure planning, explicitly authorized application.

SQLite EXCLUSIVE reservations exclude controller publication and lifecycle writes.
Never deletes evidence, logs, backups, temporary files or unrecognized data.
A durable pending journal records every deletion intent before the first unlink;
a pending intent blocks all further housekeeping until manual reconciliation.
"""

import json
import os
import sqlite3
import time
from contextlib import closing
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Literal

from pydantic import ValidationError

from photoproof.contracts.artifacts import ArtifactRef
from photoproof.contracts.enums import RunStatus
from photoproof.contracts.events import EventType
from photoproof.contracts.exceptions import StorageError
from photoproof.core.cache.run_cache import CACHE_FORMAT, CacheEntry
from photoproof.core.database.store import LATEST_VERSION
from photoproof.core.storage.managed import checked_path, identity
from photoproof.core.storage.store import sha256_file

if TYPE_CHECKING:
    from photoproof.core.bootstrap import Application


JOURNAL_NAME = "housekeeping/pending.jsonl"


def _journal_path(root: Path) -> Path:
    return checked_path(root, JOURNAL_NAME)


def _has_pending(root: Path) -> bool:
    """Return True if any housekeeping intent is still pending reconciliation."""
    path = _journal_path(root)
    if not path.exists():
        return False
    try:
        raw = path.read_bytes()
        if not raw:
            return False
        for line in raw.splitlines():
            record = json.loads(line)
            if record.get("state") == "pending":
                return True
    except (OSError, ValueError, UnicodeDecodeError):
        # Corrupt journal: treat as pending to fail closed
        return True
    return False


def _write_intent(root: Path, payload: dict[str, object]) -> None:
    """Durably record a deletion intent before any unlink."""
    path = _journal_path(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    record = {
        **payload,
        "state": "pending",
        "at": time.time(),
    }
    encoded = (json.dumps(record, sort_keys=True) + "\n").encode("utf-8")
    with open(path, "ab") as handle:
        handle.write(encoded)
        handle.flush()
        os.fsync(handle.fileno())


def _mark_completed(root: Path, identity_sha256: str) -> None:
    """Mark a pending intent as completed after successful unlink and audit."""
    path = _journal_path(root)
    if not path.exists():
        return
    lines = path.read_bytes().splitlines()
    updated = []
    for line in lines:
        record = json.loads(line)
        if record.get("identity_sha256") == identity_sha256 and record.get("state") == "pending":
            record["state"] = "completed"
            record["completed_at"] = time.time()
        updated.append(json.dumps(record, sort_keys=True))
    path.write_text("\n".join(updated) + "\n")


@dataclass(frozen=True)
class Candidate:
    data_class: Literal["cache", "artifact", "run"]
    relative_path: str
    sha256: str
    size: int
    age_days: float
    reason: str
    file_identity: tuple[int, int, int, int]
    cache_key: str | None = None
    run_id: str | None = None
    protected_references: tuple[str, ...] = ()


@dataclass(frozen=True)
class Plan:
    candidates: tuple[Candidate, ...]
    blocked: tuple[str, ...]
    protected_artifacts: tuple[str, ...]
    observed_at: float

    @property
    def reclaimed_bytes(self) -> int:
        return sum(item.size for item in self.candidates)


def _hex(value: str, length: int) -> str:
    if len(value) != length or any(c not in "0123456789abcdef" for c in value):
        raise StorageError("invalid stored identifier; run doctor before housekeeping")
    return value


def _timestamp(value: str) -> float:
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        raise StorageError("retention timestamp has no timezone")
    return parsed.timestamp()


def _output_references(value: object) -> set[str]:
    """Validate serialized ArtifactRef-shaped output objects, including nested values."""
    found: set[str] = set()
    if isinstance(value, dict):
        if {"sha256", "producer", "name", "media_type", "size"} <= value.keys():
            found.add(ArtifactRef.model_validate(value).sha256)
        else:
            for child in value.values():
                found.update(_output_references(child))
    elif isinstance(value, list):
        for child in value:
            found.update(_output_references(child))
    return found


class Housekeeping:
    """Plan from an existing database without bootstrap/startup mutations."""

    def __init__(self, app: "Application") -> None:
        self.app = app

    def plan(self, *, now: float | None = None) -> Plan:
        observed = time.time() if now is None else now
        path = self.app.database_path
        if not path.exists():
            return Plan((), ("database absent: references cannot be established",), (), observed)
        # A pending housekeeping intent blocks all further housekeeping
        # until manual reconciliation.
        if _has_pending(self.app.storage.root):
            return Plan(
                (),
                (
                    "housekeeping intent pending reconciliation; "
                    "inspect ARE/housekeeping/pending.jsonl and resolve manually",
                ),
                (),
                observed,
            )
        try:
            checked_path(self.app.storage.root, path.relative_to(self.app.storage.root).as_posix())
            identity(path)
            with closing(
                sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True)
            ) as connection:
                connection.row_factory = sqlite3.Row
                connection.execute("BEGIN")
                return self._plan(connection, observed)
        except (
            OSError,
            ValueError,
            TypeError,
            KeyError,
            sqlite3.Error,
            ValidationError,
            StorageError,
        ) as error:
            return Plan(
                (), (f"reference inspection blocked: {type(error).__name__}",), (), observed
            )

    def _plan(self, connection: sqlite3.Connection, now: float) -> Plan:
        root = self.app.storage.root
        if connection.execute("PRAGMA user_version").fetchone()[0] != LATEST_VERSION:
            raise StorageError("housekeeping requires the current database schema; run doctor")
        retention = self.app.settings.retention
        if connection.execute("PRAGMA foreign_key_check").fetchone() is not None:
            raise StorageError("database references are inconsistent; run doctor")
        runs = connection.execute("SELECT * FROM runs ORDER BY run_id").fetchall()
        if any(row["finished_at"] is None for row in runs):
            return Plan((), ("unfinished runs present; cleanup requires an idle runtime",), (), now)
        protected: set[str] = set()
        run_ids: set[str] = set()
        blocked: list[str] = []
        expired_runs: list[Candidate] = []
        cache_sources = {row[0] for row in connection.execute("SELECT run_id FROM cache_entries")}
        pending_submissions = (
            connection.execute("SELECT 1 FROM submissions WHERE run_id IS NULL LIMIT 1").fetchone()
            is not None
        )
        for run in runs:
            run_id = _hex(run["run_id"], 32)
            run_ids.add(run_id)
            RunStatus(run["status"])
            path = checked_path(root, f"manifests/{run_id}.json")
            identity(path)
            if sha256_file(path) != run["manifest_sha256"]:
                raise StorageError("manifest hash mismatch; cleanup is not an integrity repair")
            manifest = json.loads(path.read_bytes())
            if not isinstance(manifest, dict):
                raise StorageError("manifest must be a mapping")
            if (
                manifest.get("manifest_version") != 1
                or manifest.get("run_id") != run_id
                or manifest.get("status") != run["status"]
            ):
                raise StorageError("manifest identity/version/status mismatch")
            references = [ArtifactRef.model_validate(value) for value in manifest["artifacts"]]
            database_refs = connection.execute(
                "SELECT * FROM artifacts WHERE run_id = ?", (run_id,)
            ).fetchall()
            expected = {(r.sha256, r.producer, r.name, r.media_type, r.size) for r in references}
            actual = {
                (r["sha256"], r["plugin_id"], r["name"], r["media_type"], r["size"])
                for r in database_refs
            }
            if expected != actual:
                raise StorageError("manifest and database artifact references disagree")
            declared = {ref.sha256 for ref in references}
            if not _output_references(manifest.get("outputs", {})) <= declared:
                raise StorageError("manifest output references an unregistered artifact")
            protected.update(declared)
            if (
                retention.run_days is not None
                and now - _timestamp(run["finished_at"]) >= retention.run_days * 86400
            ):
                reports = checked_path(root, f"reports/{run_id}")
                if run_id not in cache_sources and not reports.exists() and not pending_submissions:
                    info = identity(path)
                    expired_runs.append(
                        Candidate(
                            "run",
                            f"manifests/{run_id}.json",
                            sha256_file(path),
                            info[2],
                            (now - _timestamp(run["finished_at"])) / 86400,
                            "expired completed-run metadata; original evidence retained",
                            info,
                            run_id=run_id,
                        )
                    )
                else:
                    blocked.append(
                        f"run {run_id}: protected by cache, report data or unlinked submissions"
                    )
        manifest_dir = checked_path(root, "manifests")
        for path in manifest_dir.iterdir():
            if path.name != f"{path.stem}.json" or path.stem not in run_ids:
                raise StorageError("unregistered manifest data; reference graph is uncertain")
        # Cache files and rows must agree. Cache references protect blobs even if expired:
        # remove the cache first; a later plan can release its last reference.
        cache_rows = connection.execute("SELECT * FROM cache_entries ORDER BY cache_key").fetchall()
        keys: set[str] = set()
        candidates: list[Candidate] = []
        for row in cache_rows:
            key = _hex(row["cache_key"], 64)
            keys.add(key)
            relative = f"cache/runs/{key}.json"
            path = checked_path(root, relative)
            info = identity(path)
            entry = CacheEntry.model_validate_json(path.read_bytes())
            if (
                entry.format != CACHE_FORMAT
                or entry.key != key
                or entry.status != "COMPLETED"
                or entry.source_run_id != row["run_id"]
                or row["run_id"] not in run_ids
            ):
                raise StorageError("cache identity or source reference mismatch")
            cache_refs = {ref.sha256 for ref in entry.artifacts}
            if not _output_references(entry.outputs) <= cache_refs:
                raise StorageError("cache output references an unregistered artifact")
            protected.update(cache_refs)
            age = (now - max(_timestamp(row["created_at"]), info[3] / 1e9)) / 86400
            if retention.cache_days is not None and age >= retention.cache_days:
                candidates.append(
                    Candidate(
                        "cache",
                        relative,
                        sha256_file(path),
                        info[2],
                        age,
                        "expired disposable cache",
                        info,
                        key,
                    )
                )
        directory = checked_path(root, "cache/runs")
        if directory.exists():
            for path in directory.iterdir():
                if path.name != f"{path.stem}.json" or path.stem not in keys:
                    raise StorageError("unregistered cache files; reference graph is uncertain")
        # DB references are a separate protection source, even if another row is malformed.
        protected.update(row[0] for row in connection.execute("SELECT sha256 FROM artifacts"))
        for digest in sorted(protected):
            path = checked_path(root, f"artifacts/{_hex(digest, 64)[:2]}/{digest}")
            identity(path)
            if sha256_file(path) != digest:
                raise StorageError(
                    "referenced artifact absent or corrupt; preserve for investigation"
                )
        artifacts = checked_path(root, "artifacts")
        for prefix in sorted(artifacts.iterdir()):
            checked_path(root, prefix.relative_to(root).as_posix())
            if not prefix.is_dir() or len(prefix.name) != 2:
                raise StorageError("unrecognized artifact directory")
            for artifact_path in sorted(prefix.iterdir()):
                digest = _hex(artifact_path.name, 64)
                relative = f"artifacts/{digest[:2]}/{digest}"
                if prefix.name != digest[:2]:
                    raise StorageError("artifact content address mismatch")
                path = checked_path(root, relative)
                info = identity(path)
                if sha256_file(path) != digest:
                    raise StorageError("artifact hash mismatch; cleanup is not an integrity repair")
                age = (now - info[3] / 1e9) / 86400
                if (
                    digest not in protected
                    and retention.artifact_days is not None
                    and age >= retention.artifact_days
                ):
                    # A matching original remains protected, even under another directory entry.
                    if self.app.storage.blob_path("objects", digest).exists():
                        continue
                    candidates.append(
                        Candidate(
                            "artifact",
                            relative,
                            digest,
                            info[2],
                            age,
                            "expired unreferenced derived artifact",
                            info,
                        )
                    )
        return Plan(
            tuple([*candidates, *expired_runs]), tuple(blocked), tuple(sorted(protected)), now
        )

    def apply(self, approved: Plan, *, authorized: bool = False) -> tuple[str, ...]:
        """Apply exactly an approved plan, stopping on the first changed/failed operation.

        A durable intent precedes unlink. A failed outcome append is an error, never success.
        A crash after unlink can leave a stale cache row. Subsequent planning fails closed
        and requires manual inspection of the durable audit intent. No retained references change.
        """
        if not authorized:
            raise StorageError("housekeeping apply requires explicit operator authorization")
        # A pending intent from an earlier interrupted run blocks reapplication
        # until manual reconciliation.
        if _has_pending(self.app.storage.root):
            raise StorageError(
                "housekeeping intent pending reconciliation; "
                "inspect ARE/housekeeping/pending.jsonl and resolve manually"
            )
        if not approved.candidates:
            if approved.blocked:
                raise StorageError("housekeeping blocked; inspect the dry-run findings")
            return ()
        path = checked_path(
            self.app.storage.root,
            self.app.database_path.relative_to(self.app.storage.root).as_posix(),
        )
        identity(path)
        outcomes: list[str] = []
        try:
            with closing(
                sqlite3.connect(
                    path.resolve().as_uri() + "?mode=rw", uri=True, timeout=5, isolation_level=None
                )
            ) as connection:
                connection.row_factory = sqlite3.Row
                connection.execute("PRAGMA foreign_keys = ON")
                connection.execute("BEGIN EXCLUSIVE")
                try:
                    current = self._plan(connection, approved.observed_at)
                    if (
                        current.candidates != approved.candidates
                        or current.protected_artifacts != approved.protected_artifacts
                    ):
                        raise StorageError("runtime changed since planning; run housekeeping again")
                    for item in approved.candidates:
                        target = checked_path(self.app.storage.root, item.relative_path)
                        if (
                            identity(target) != item.file_identity
                            or sha256_file(target) != item.sha256
                        ):
                            raise StorageError("candidate changed; refusing deletion")
                        payload = {
                            "data_class": item.data_class,
                            "identity_sha256": item.sha256,
                            "record_id": item.cache_key or item.run_id or item.sha256,
                            "reason": item.reason,
                            "size": item.size,
                        }
                        self.app.bus.emit(
                            EventType.HOUSEKEEPING_APPLIED,
                            data={**payload, "outcome": "intent", "reclaimed_bytes": 0},
                        )
                        # Durable intent before any unlink: if the process crashes
                        # after unlink but before audit, the pending journal preserves
                        # the destruction record for manual reconciliation.
                        _write_intent(
                            self.app.storage.root,
                            {
                                **payload,
                                "relative_path": item.relative_path,
                                "file_identity": item.file_identity,
                            },
                        )
                        try:
                            # No chmod: a read-only derived blob that cannot be unlinked stays
                            # in place. Retained evidence permissions are never changed.
                            checked_path(self.app.storage.root, item.relative_path)
                            if (
                                identity(target) != item.file_identity
                                or sha256_file(target) != item.sha256
                            ):
                                raise StorageError(
                                    "candidate changed during audit; refusing deletion"
                                )
                            target.unlink()
                            if item.cache_key is not None:
                                connection.execute(
                                    "DELETE FROM cache_entries WHERE cache_key = ?",
                                    (item.cache_key,),
                                )
                            if item.run_id is not None:
                                connection.execute(
                                    "UPDATE submissions SET run_id = NULL WHERE run_id = ?",
                                    (item.run_id,),
                                )
                                connection.execute(
                                    "DELETE FROM artifacts WHERE run_id = ?", (item.run_id,)
                                )
                                connection.execute(
                                    "DELETE FROM plugin_executions WHERE run_id = ?", (item.run_id,)
                                )
                                connection.execute(
                                    "DELETE FROM runs WHERE run_id = ?", (item.run_id,)
                                )
                        except (OSError, StorageError):
                            self.app.bus.emit(
                                EventType.HOUSEKEEPING_APPLIED,
                                data={**payload, "outcome": "failed", "reclaimed_bytes": 0},
                            )
                            raise
                        self.app.bus.emit(
                            EventType.HOUSEKEEPING_APPLIED,
                            data={**payload, "outcome": "deleted", "reclaimed_bytes": item.size},
                        )
                        # Mark the durable intent as completed only after the
                        # unlink and its audit outcome have both succeeded.
                        _mark_completed(self.app.storage.root, item.sha256)
                        outcomes.append(item.sha256)
                    connection.execute("COMMIT")
                except BaseException:
                    # Every metadata removal follows a successful, explicitly authorized
                    # unlink. Preserve earlier completed removals if a later unlink/audit
                    # fails; rolling them back would manufacture missing-manifest records.
                    connection.execute("COMMIT")
                    raise
        except (OSError, sqlite3.Error, ValueError, TypeError, KeyError, ValidationError) as error:
            raise StorageError(
                "housekeeping incomplete; preserve audit intents and inspect runtime"
            ) from error
        return tuple(outcomes)
