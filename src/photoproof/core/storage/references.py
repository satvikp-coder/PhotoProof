"""Original-evidence references supplement the shared #55 manifest/artifact graph."""

import json
import sqlite3
from dataclasses import dataclass
from typing import TYPE_CHECKING

from photoproof.contracts.analysis import InputDescriptor
from photoproof.contracts.exceptions import StorageError
from photoproof.core.storage.managed import checked_path, identity
from photoproof.core.storage.store import sha256_file

if TYPE_CHECKING:
    from photoproof.core.bootstrap import Application


@dataclass(frozen=True)
class EvidenceReferences:
    runs: dict[str, frozenset[str]]
    sizes: dict[str, int]
    first_seen: dict[str, str]
    submissions: dict[str, tuple[tuple[int, str | None, str], ...]]


def evidence_references(app: "Application", connection: sqlite3.Connection) -> EvidenceReferences:
    """Require agreement of manifest inputs, evidence, submissions and original bytes.

    Caller first validates #55's full graph and holds the same SQLite transaction.
    Unknown originals and missing associations are uncertainty, never deletion permission.
    """
    rows = connection.execute("SELECT * FROM evidence ORDER BY sha256").fetchall()
    sizes = {row["sha256"]: row["size"] for row in rows}
    first_seen = {row["sha256"]: row["first_seen_at"] for row in rows}
    submissions: dict[str, list[tuple[int, str | None, str]]] = {sha: [] for sha in sizes}
    for row in connection.execute("SELECT id,sha256,run_id,role FROM submissions ORDER BY id"):
        if row["sha256"] not in sizes:
            raise StorageError("submission lacks an evidence record; run doctor")
        submissions[row["sha256"]].append((row["id"], row["run_id"], row["role"]))
    runs: dict[str, frozenset[str]] = {}
    for row in connection.execute("SELECT run_id FROM runs ORDER BY run_id"):
        run_id = row["run_id"]
        path = checked_path(app.storage.root, f"manifests/{run_id}.json")
        manifest = json.loads(path.read_bytes())
        inputs = [InputDescriptor.model_validate(value) for value in manifest["inputs"]]
        if not inputs or len({item.role for item in inputs}) != len(inputs):
            raise StorageError("manifest input roles are missing or ambiguous")
        pairs = {(item.sha256, item.role) for item in inputs}
        recorded = {
            (sha, role)
            for sha, refs in submissions.items()
            for _, owner, role in refs
            if owner == run_id
        }
        if pairs != recorded:
            raise StorageError("manifest inputs and submission references disagree; run doctor")
        for item in inputs:
            if sizes.get(item.sha256) != item.size:
                raise StorageError("manifest input size disagrees with evidence metadata")
        runs[run_id] = frozenset(item.sha256 for item in inputs)
    known = set(sizes)
    base = checked_path(app.storage.root, "objects")
    discovered: set[str] = set()
    for prefix in sorted(base.iterdir()):
        checked_path(app.storage.root, prefix.relative_to(app.storage.root).as_posix())
        if not prefix.is_dir():
            raise StorageError("unknown object-store entry; inspect before deletion")
        for path in sorted(prefix.iterdir()):
            sha = path.name
            expected = app.storage.blob_path("objects", sha)
            checked_path(app.storage.root, path.relative_to(app.storage.root).as_posix())
            if prefix.name != sha[:2] or expected != path.resolve() or sha not in known:
                raise StorageError("unregistered evidence object; inspect before deletion")
            info = identity(path)
            if info[2] != sizes[sha] or sha256_file(path) != sha:
                raise StorageError(
                    "original evidence integrity mismatch; preserve for investigation"
                )
            discovered.add(sha)
    if discovered != known:
        raise StorageError("evidence record has no verified original blob")
    return EvidenceReferences(
        runs, sizes, first_seen, {sha: tuple(refs) for sha, refs in submissions.items()}
    )
