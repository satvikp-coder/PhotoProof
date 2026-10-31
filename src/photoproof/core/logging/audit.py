"""Serialized JSONL audit appends; crash fragments are findings, never silently truncated."""

import json
import os
from contextlib import nullcontext
from pathlib import Path

from photoproof.contracts.exceptions import StorageError
from photoproof.core.security.paths import safe_join
from photoproof.core.storage.locking import file_lock


def append_record(log_dir: Path, record: dict[str, object]) -> None:
    """Append one complete UTF-8 record under an OS lock and fsync it before success.

    A crash/power failure can leave a fragment. Refuse subsequent appends until an
    operator preserves and investigates it; never concatenate a new entry onto it.
    """
    data = json.dumps(record, default=str, sort_keys=True).encode("utf-8") + b"\n"
    with file_lock(log_dir, name=".audit.lock", exclusive=True):
        path = safe_join(log_dir, "audit.jsonl")
        with path.open("a+b") as handle:
            if handle.seek(0, os.SEEK_END):
                handle.seek(-1, os.SEEK_END)
                if handle.read(1) != b"\n":
                    raise StorageError(
                        f"{path}: incomplete audit record; preserve and inspect manually"
                    )
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())


def audit_problems(log_dir: Path) -> list[str]:
    """Read-only validation. JSON object records and newline boundaries are required."""
    path = safe_join(log_dir, "audit.jsonl")
    if not path.exists():
        return []
    # All current writers create this before writing the audit file. For a legacy file,
    # validate read-only without creating a coordination file during doctor.
    guard = (
        file_lock(log_dir, name=".audit.lock", exclusive=False, create=False)
        if (log_dir / ".audit.lock").exists()
        else nullcontext()
    )
    problems: list[str] = []
    with guard, path.open("rb") as handle:
        for number, line in enumerate(handle, 1):
            try:
                if not line.endswith(b"\n") or not isinstance(json.loads(line), dict):
                    raise ValueError("incomplete/non-object record")
            except (ValueError, UnicodeDecodeError):
                problems.append(f"line {number}: invalid or incomplete audit record")
    return problems
