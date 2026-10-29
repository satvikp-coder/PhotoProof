"""Run-level result cache under ``ARE/cache/runs/<key>.json``.

The key is a SHA-256 over every identity that can change a result (see
``cache_key``). An entry is reused only if it parses, matches the expected shape,
and every referenced artifact still exists with the right hash. Anything else is
invalidated (cache entries are disposable) and treated as a miss. Only COMPLETED
runs are stored.
"""

import logging
from collections.abc import Mapping
from typing import Any

from pydantic import BaseModel, ConfigDict, ValidationError

from photoproof.contracts.artifacts import ArtifactRef
from photoproof.contracts.exceptions import StorageError
from photoproof.core.database.store import Database
from photoproof.core.storage.store import RuntimeStorage, canonical_json, sha256_bytes

CACHE_FORMAT = 1
_log = logging.getLogger(__name__)


class CacheEntry(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    format: int
    key: str
    status: str
    source_run_id: str
    executions: dict[str, dict[str, Any]]
    outputs: dict[str, Any]
    artifacts: list[ArtifactRef]


def cache_key(components: Mapping[str, object]) -> str:
    """Components must include inputs, mode, profile, effective configuration, plugin
    versions and implementation digests, dependency versions and the pipeline
    fingerprint. The image hash alone is never a sufficient key."""
    return sha256_bytes(canonical_json({"format": CACHE_FORMAT, **components}))


class RunCache:
    def __init__(self, storage: RuntimeStorage, db: Database | None) -> None:
        self._storage = storage
        self._db = db

    def _parts(self, key: str) -> tuple[str, str, str]:
        if len(key) != 64:
            raise StorageError(f"invalid cache key {key!r}")
        return ("cache", "runs", f"{key}.json")

    def _read(
        self, key: str, *, verify_artifacts: bool = True
    ) -> tuple[CacheEntry | None, str | None]:
        """Parse and validate an entry: ``(entry, None)`` or ``(None, problem)``."""
        try:
            entry = CacheEntry.model_validate(self._storage.read_json(*self._parts(key)))
        except StorageError as error:
            return None, str(error)
        except ValidationError as error:
            return None, f"entry has an invalid shape ({error.error_count()} errors)"
        if entry.format != CACHE_FORMAT or entry.key != key:
            return None, "entry format or key does not match"
        if entry.status != "COMPLETED":
            return None, "entry records a run that did not complete"
        for artifact in entry.artifacts if verify_artifacts else ():
            if not self._storage.verify_blob("artifacts", artifact.sha256):
                return None, f"artifact {artifact.sha256} is missing or fails its hash check"
        return entry, None

    def problem(self, key: str, *, verify_artifacts: bool = True) -> str | None:
        """Why the entry for ``key`` is unusable, or ``None`` if it is valid. Without
        ``verify_artifacts`` only the entry itself is checked (fast; used at startup —
        ``lookup`` always verifies before reuse)."""
        return self._read(key, verify_artifacts=verify_artifacts)[1]

    def lookup(self, key: str) -> CacheEntry | None:
        if not self._storage.path(*self._parts(key)).is_file():
            return None
        entry, problem = self._read(key)
        if problem:
            self.invalidate(key, problem)
        return entry

    def store(
        self,
        key: str,
        *,
        run_id: str,
        executions: dict[str, dict[str, Any]],
        outputs: dict[str, Any],
        artifacts: list[ArtifactRef],
    ) -> None:
        entry = CacheEntry(
            format=CACHE_FORMAT,
            key=key,
            status="COMPLETED",
            source_run_id=run_id,
            executions=executions,
            outputs=outputs,
            artifacts=artifacts,
        )
        self._storage.write_json(entry.model_dump(mode="json"), *self._parts(key))
        if self._db is not None:
            self._db.record_cache_entry(key, run_id)

    def invalidate(self, key: str, reason: str) -> None:
        _log.warning("invalidating cache entry", extra={"cache_key": key, "reason": reason})
        self._storage.path(*self._parts(key)).unlink(missing_ok=True)
        if self._db is not None:
            self._db.delete_cache_entry(key)

    def entry_keys(self) -> list[str]:
        directory = self._storage.root / "cache" / "runs"
        return sorted(p.stem for p in directory.glob("*.json")) if directory.is_dir() else []
