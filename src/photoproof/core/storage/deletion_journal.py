"""Persistent privacy-operation receipts: uncertain operations require manual reconciliation."""

import re
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from photoproof.contracts.exceptions import StorageError
from photoproof.core.storage.managed import checked_path, identity
from photoproof.core.storage.store import atomic_write, canonical_json


class Journal(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    operation_id: str = Field(pattern=r"^[0-9a-f]{32}$")
    run_id: str | None = Field(default=None, pattern=r"^[0-9a-f]{32}$")
    phase: Literal["pending", "complete", "cancelled"] = "pending"
    plan_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    with_evidence: bool = False


def journals(root: Path) -> tuple[Journal, ...]:
    directory = checked_path(root, "database/deletions")
    if not directory.exists():
        return ()
    result = []
    for path in sorted(directory.iterdir()):
        checked_path(root, path.relative_to(root).as_posix())
        identity(path)
        if not re.fullmatch(r"[0-9a-f]{32}\.json", path.name):
            raise StorageError("unknown deletion journal; preserve it and reconcile manually")
        try:
            item = Journal.model_validate_json(path.read_bytes())
        except ValueError as error:
            raise StorageError(
                "invalid deletion journal; preserve it and reconcile manually"
            ) from error
        if item.operation_id != path.stem:
            raise StorageError("deletion journal identity mismatch")
        result.append(item)
    return tuple(result)


def require_reconciled(root: Path, *, current: Journal | None = None) -> tuple[Journal, ...]:
    result = journals(root)
    if any(item.phase == "pending" and item != current for item in result):
        raise StorageError(
            "uncertain deletion intent remains; preserve receipts "
            "and reconcile manually before retrying"
        )
    return result


def write_journal(root: Path, item: Journal) -> None:
    directory = checked_path(root, "database/deletions")
    directory.mkdir(mode=0o700, exist_ok=True)
    target = checked_path(root, f"database/deletions/{item.operation_id}.json")
    if target.exists():
        identity(target)
        try:
            previous = Journal.model_validate_json(target.read_bytes())
        except ValueError as error:
            raise StorageError("journal is unreadable; refusing overwrite") from error
        if (
            previous.phase != "pending"
            or item.phase == "pending"
            or previous != item.model_copy(update={"phase": "pending"})
        ):
            raise StorageError("journal transition is not authorized; refusing overwrite")
    atomic_write(target, canonical_json(item.model_dump(mode="json")) + b"\n")
