"""Housekeeping service: retention policies and storage quotas for ARE/.

Implements age-based retention and size-based quotas for runtime data.
Deletions are always of specific files only (never recursive), and every
deletion is audited. Dry run is the default.
"""

from __future__ import annotations

import contextlib
import datetime
import os
import time
from pathlib import Path
from typing import Any

from photoproof.contracts.enums import Severity
from photoproof.core.config.validator import QuotaSettings, RetentionSettings
from photoproof.core.database.store import Database
from photoproof.core.storage.store import RuntimeStorage


class HousekeepingReport:
    """Result of a housekeeping run."""

    def __init__(self, dry_run: bool) -> None:
        self.dry_run = dry_run
        self.actions: list[dict[str, Any]] = []
        self.errors: list[str] = []
        self.bytes_freed = 0

    def add_action(
        self,
        kind: str,
        path: str,
        reason: str,
        size: int = 0,
        deleted: bool = False,
    ) -> None:
        self.actions.append(
            {
                "kind": kind,
                "path": path,
                "reason": reason,
                "size": size,
                "deleted": deleted,
                "timestamp": datetime.datetime.now(datetime.UTC).isoformat(),
            }
        )
        if deleted:
            self.bytes_freed += size

    def add_error(self, message: str) -> None:
        self.errors.append(message)

    def to_dict(self) -> dict[str, Any]:
        return {
            "dry_run": self.dry_run,
            "actions": self.actions,
            "errors": self.errors,
            "bytes_freed": self.bytes_freed,
            "action_count": len(self.actions),
        }


class HousekeepingService:
    """Enforces retention policies and storage quotas on ARE/."""

    def __init__(
        self,
        storage: RuntimeStorage,
        db: Database | None,
        retention: RetentionSettings,
        quotas: QuotaSettings,
    ) -> None:
        self.storage = storage
        self.db = db
        self.retention = retention
        self.quotas = quotas

    def run(self, apply: bool = False) -> HousekeepingReport:
        """Run housekeeping. When apply=False, only reports what would be deleted."""
        report = HousekeepingReport(dry_run=not apply)

        # Check quota
        total_size = self._directory_size(self.storage.root)
        quota_percent = (total_size / (self.quotas.max_total_mb * 1024 * 1024)) * 100

        if quota_percent >= 100:
            report.add_error(
                f"storage quota at {quota_percent:.1f}% (>= 100%), cannot safely delete"
            )
        elif quota_percent >= self.quotas.warn_at_percent:
            report.add_action(
                "quota_warning",
                str(self.storage.root),
                f"storage at {quota_percent:.1f}% of {self.quotas.max_total_mb} MiB",
            )

        # Age-based retention: cache entries
        cache_dir = self.storage.root / "cache"
        if cache_dir.is_dir():
            self._retention_scan(
                cache_dir,
                "cache_entry",
                self.retention.cache_days,
                report,
                apply,
            )

        # Age-based retention: artifacts
        artifacts_dir = self.storage.root / "artifacts"
        if artifacts_dir.is_dir():
            self._retention_scan(
                artifacts_dir,
                "artifact",
                self.retention.artifact_days,
                report,
                apply,
            )

        # Age-based retention: runs and their derived artifacts
        if self.retention.run_days is not None and self.db is not None:
            cutoff = (
                datetime.datetime.now(datetime.UTC)
                - datetime.timedelta(days=self.retention.run_days)
            ).isoformat()
            try:
                old_runs = self.db.unfinished_runs(older_than=cutoff)
                for run in old_runs:
                    run_id = str(run["run_id"])
                    # Check if process is still alive
                    pid = run.get("pid")
                    if pid and self._process_alive(pid):
                        continue
                    report.add_action(
                        "run",
                        run_id,
                        f"run older than {self.retention.run_days} days",
                    )
            except Exception:  # noqa: BLE001 - report, don't crash
                report.add_error("could not query old runs from database")

        return report

    def _retention_scan(
        self,
        directory: Path,
        kind: str,
        max_age_days: int,
        report: HousekeepingReport,
        apply: bool,
    ) -> None:
        """Scan a directory for files older than max_age_days."""
        cutoff = time.time() - max_age_days * 86400
        for item in directory.rglob("*"):
            if not item.is_file():
                continue
            try:
                mtime = item.stat().st_mtime
            except OSError:
                continue
            if mtime < cutoff:
                size = item.stat().st_size
                reason = f"older than {max_age_days} days"
                if apply:
                    try:
                        # Restore write permission on read-only blobs
                        item.chmod(0o600)
                        item.unlink()
                        report.add_action(kind, str(item), reason, size, deleted=True)
                    except OSError as error:
                        report.add_error(f"could not delete {item}: {error}")
                else:
                    report.add_action(kind, str(item), reason, size, deleted=False)

    def _directory_size(self, directory: Path) -> int:
        """Compute total size of all files under directory."""
        total = 0
        if directory.is_dir():
            for item in directory.rglob("*"):
                if item.is_file():
                    with contextlib.suppress(OSError):
                        total += item.stat().st_size
        return total

    def _process_alive(self, pid: Any) -> bool:
        """Check if a process with the given PID is still running."""
        if pid is None:
            return False
        try:
            os.kill(int(pid), 0)
        except (ProcessLookupError, PermissionError, OSError, TypeError, ValueError):
            return False
        return True


def check_quota_health(storage: RuntimeStorage, quotas: QuotaSettings) -> tuple[Severity, str]:
    """Check storage quota health. Returns (severity, message)."""
    total = 0
    if storage.root.is_dir():
        for item in storage.root.rglob("*"):
            if item.is_file():
                with contextlib.suppress(OSError):
                    total += item.stat().st_size

    max_bytes = quotas.max_total_mb * 1024 * 1024
    if max_bytes == 0:
        return Severity.OK, "no quota configured"

    percent = (total / max_bytes) * 100
    if percent >= 100:
        return Severity.ERROR, f"storage at {percent:.1f}% of {quotas.max_total_mb} MiB"
    if percent >= quotas.warn_at_percent:
        return Severity.WARNING, f"storage at {percent:.1f}% of {quotas.max_total_mb} MiB"
    return Severity.OK, f"storage at {percent:.1f}% of {quotas.max_total_mb} MiB"
