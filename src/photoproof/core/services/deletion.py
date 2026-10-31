"""Explicit, hash-audited run retirement on top of the #55 reference validator."""

import sqlite3
import time
import uuid
from contextlib import closing
from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal

from pydantic import ValidationError

from photoproof.contracts.events import EventType
from photoproof.contracts.exceptions import EventDeliveryError, StorageError
from photoproof.core.cache.run_cache import CacheEntry
from photoproof.core.services.housekeeping import Candidate, Housekeeping, _hex
from photoproof.core.storage.deletion_journal import Journal, require_reconciled, write_journal
from photoproof.core.storage.managed import checked_path, identity, require_local_runtime
from photoproof.core.storage.references import evidence_references
from photoproof.core.storage.store import (
    _fsync_directory,
    canonical_json,
    sha256_bytes,
    sha256_file,
)

if TYPE_CHECKING:
    from photoproof.core.bootstrap import Application


@dataclass(frozen=True)
class DeletionPlan:
    run_id: str
    with_evidence: bool
    files: tuple[Candidate, ...]
    submissions: tuple[int, ...]

    @property
    def fingerprint(self) -> str:
        return sha256_bytes(
            canonical_json(
                {
                    "run_id": self.run_id,
                    "with_evidence": self.with_evidence,
                    "files": [
                        (f.data_class, f.sha256, f.file_identity, f.relative_path)
                        for f in self.files
                    ],
                    "submissions": self.submissions,
                }
            )
        )


class DeletionService:
    def __init__(self, app: "Application") -> None:
        self.app = app

    def _candidate(
        self,
        kind: Literal["cache", "artifact", "run", "report", "evidence"],
        relative: str,
        *,
        cache_key: str | None = None,
    ) -> Candidate:
        path = checked_path(self.app.storage.root, relative)
        info = identity(path)
        return Candidate(
            kind,
            relative,
            sha256_file(path),
            info[2],
            0,
            "operator_delete",
            info,
            cache_key=cache_key,
        )

    def _plan(
        self,
        connection: sqlite3.Connection,
        run_id: str,
        *,
        with_evidence: bool,
        current: Journal | None = None,
    ) -> DeletionPlan:
        # Both services use this exact validator; this is not another artifact graph.
        Housekeeping(self.app).reference_plan(connection, now=time.time(), current=current)
        graph = evidence_references(self.app, connection)
        if run_id not in graph.runs:
            raise StorageError("run does not exist; no deletion performed")
        run_hashes = graph.runs[run_id]
        if with_evidence:
            for sha in run_hashes:
                refs = graph.submissions[sha]
                if (
                    len(refs) != 1
                    or refs[0][1] != run_id
                    or any(sha in hashes for owner, hashes in graph.runs.items() if owner != run_id)
                ):
                    raise StorageError(
                        "original is shared or has pending/multiple submissions; "
                        "use delete without --with-evidence to retain it"
                    )
        selected = connection.execute(
            "SELECT * FROM artifacts WHERE run_id = ? ORDER BY sha256", (run_id,)
        ).fetchall()
        selected_hashes = {row["sha256"] for row in selected}
        protected = {
            row[0]
            for row in connection.execute(
                "SELECT sha256 FROM artifacts WHERE run_id != ?", (run_id,)
            )
        }
        files: list[Candidate] = []
        for row in connection.execute("SELECT * FROM cache_entries ORDER BY cache_key"):
            key = _hex(row["cache_key"], 64)
            path = checked_path(self.app.storage.root, f"cache/runs/{key}.json")
            entry = CacheEntry.model_validate_json(path.read_bytes())
            if row["run_id"] == run_id:
                files.append(self._candidate("cache", f"cache/runs/{key}.json", cache_key=key))
            else:
                protected.update(ref.sha256 for ref in entry.artifacts)
        reports_root = checked_path(self.app.storage.root, "reports")
        for directory in reports_root.iterdir():
            checked_path(
                self.app.storage.root, directory.relative_to(self.app.storage.root).as_posix()
            )
            # Report contracts are not yet implemented on this base; retained/unknown
            # report data conservatively protects artifacts, while empty directories do not.
            if directory.name != run_id and (
                not directory.is_dir() or next(directory.iterdir(), None) is not None
            ):
                protected.update(selected_hashes)
        directory = checked_path(self.app.storage.root, f"reports/{run_id}")
        if directory.exists():
            if not directory.is_dir():
                raise StorageError("run reports are not an ordinary directory")
            for report in sorted(directory.iterdir()):
                if report.name not in {"report.json", "report.html", "index.html"}:
                    raise StorageError("unknown run report entry; inspect it before deletion")
                files.append(self._candidate("report", f"reports/{run_id}/{report.name}"))
        for sha in sorted(selected_hashes - protected):
            files.append(self._candidate("artifact", f"artifacts/{sha[:2]}/{sha}"))
        files.append(self._candidate("run", f"manifests/{run_id}.json"))
        if with_evidence:
            for sha in sorted(run_hashes):
                files.append(self._candidate("evidence", f"objects/{sha[:2]}/{sha}"))
        submissions = tuple(
            row[0]
            for row in connection.execute(
                "SELECT id FROM submissions WHERE run_id = ? ORDER BY id", (run_id,)
            )
        )
        return DeletionPlan(run_id, with_evidence, tuple(files), submissions)

    def delete(self, run_id: str, *, with_evidence: bool = False) -> str:
        """Explicit invocation authorizes this one run, never shared originals or other runs.

        Journal remains pending after any destructive uncertainty. There is no cross-filesystem
        rollback, secure media erasure, or automatic recovery of interrupted operations.
        """
        _hex(run_id, 32)
        root = self.app.storage.root
        try:
            if not root.is_dir():
                raise StorageError("runtime is absent; no run can be deleted")
            require_local_runtime(root)
            previous = require_reconciled(root)
            database = checked_path(root, self.app.database_path.relative_to(root).as_posix())
            identity(database)
        except OSError as error:
            raise StorageError(
                "runtime/database inaccessible; inspect permissions before deletion"
            ) from error
        operation: Journal | None = None
        destructive = False
        committed = False
        try:
            with closing(
                sqlite3.connect(
                    database.as_uri() + "?mode=rw", uri=True, timeout=5, isolation_level=None
                )
            ) as connection:
                connection.row_factory = sqlite3.Row
                connection.execute("PRAGMA foreign_keys = ON")
                connection.execute("BEGIN EXCLUSIVE")
                try:
                    require_reconciled(root)
                    if (
                        connection.execute(
                            "SELECT 1 FROM runs WHERE run_id = ?", (run_id,)
                        ).fetchone()
                        is None
                    ):
                        if any(
                            j.run_id == run_id
                            and j.phase == "complete"
                            and j.with_evidence == with_evidence
                            for j in previous
                        ):
                            connection.execute("COMMIT")
                            return "already deleted; original receipt retained"
                        raise StorageError("run does not exist; no deletion performed")
                    approved = self._plan(connection, run_id, with_evidence=with_evidence)
                    operation = Journal(
                        operation_id=uuid.uuid4().hex,
                        run_id=run_id,
                        plan_sha256=approved.fingerprint,
                        with_evidence=with_evidence,
                    )
                    write_journal(root, operation)
                    payload = {
                        "operation_id": operation.operation_id,
                        "run_id": run_id,
                        "reason": "operator_delete",
                        "count": len(approved.files),
                        "records": len(approved.submissions),
                    }
                    self.app.bus.emit(
                        EventType.DELETION_APPLIED, data={**payload, "outcome": "intent"}
                    )
                    # Audit subscribers cannot change the authorized graph or file identities.
                    # Ignore this operation's own pending marker only during its reservation.
                    current = self._plan(
                        connection, run_id, with_evidence=with_evidence, current=operation
                    )
                    if current != approved:
                        raise StorageError("run changed during authorization; deletion refused")
                    for item in approved.files:
                        target = checked_path(root, item.relative_path)
                        if (
                            identity(target) != item.file_identity
                            or sha256_file(target) != item.sha256
                        ):
                            raise StorageError("approved file changed; deletion refused")
                        event = {
                            **payload,
                            "data_class": item.data_class,
                            "identity_sha256": item.sha256,
                            "size": item.size,
                        }
                        self.app.bus.emit(
                            EventType.DELETION_APPLIED,
                            data={**event, "outcome": "intent", "reclaimed_bytes": 0},
                        )
                        checked_path(root, item.relative_path)
                        if (
                            identity(target) != item.file_identity
                            or sha256_file(target) != item.sha256
                        ):
                            raise StorageError(
                                "approved file changed after audit; deletion refused"
                            )
                        target.unlink()  # Never chmod evidence (including Windows READONLY).
                        destructive = True
                        _fsync_directory(target.parent)
                        self.app.bus.emit(
                            EventType.DELETION_APPLIED,
                            data={**event, "outcome": "deleted", "reclaimed_bytes": item.size},
                        )
                    connection.execute("DELETE FROM cache_entries WHERE run_id = ?", (run_id,))
                    connection.execute("DELETE FROM artifacts WHERE run_id = ?", (run_id,))
                    connection.execute("DELETE FROM plugin_executions WHERE run_id = ?", (run_id,))
                    connection.execute("DELETE FROM submissions WHERE run_id = ?", (run_id,))
                    connection.execute("DELETE FROM runs WHERE run_id = ?", (run_id,))
                    if with_evidence:
                        for item in approved.files:
                            if item.data_class == "evidence":
                                connection.execute(
                                    "DELETE FROM evidence WHERE sha256 = ?", (item.sha256,)
                                )
                    connection.execute("COMMIT")
                    committed = True
                    self.app.bus.emit(
                        EventType.DELETION_APPLIED, data={**payload, "outcome": "complete"}
                    )
                    write_journal(root, operation.model_copy(update={"phase": "complete"}))
                except BaseException as error:
                    if connection.in_transaction:
                        connection.execute("ROLLBACK")
                    if operation is not None and not destructive and not committed:
                        write_journal(root, operation.model_copy(update={"phase": "cancelled"}))
                    if operation is not None:
                        category = (
                            "permission_denied"
                            if isinstance(error, PermissionError)
                            else "audit_error"
                            if isinstance(error, EventDeliveryError)
                            else "storage_error"
                        )
                        self.app.bus.emit(
                            EventType.DELETION_APPLIED,
                            data={
                                "operation_id": operation.operation_id,
                                "run_id": run_id,
                                "outcome": "failed",
                                "failure_category": category,
                            },
                        )
                    raise
        except EventDeliveryError as error:
            raise EventDeliveryError(
                "deletion audit failed; preserve receipts and inspect the journal phase. "
                "Reconcile pending intent manually; only cancelled work is safe to retry"
            ) from error
        except (OSError, sqlite3.Error, ValueError, TypeError, KeyError, ValidationError) as error:
            raise StorageError(
                "deletion incomplete/refused; preserve journal and audit receipts. "
                "Inspect permissions and reconcile pending intent manually"
            ) from error
        return "run deleted; " + (
            "unshared originals deleted"
            if with_evidence
            else "originals retained and may still occupy disk space"
        )
