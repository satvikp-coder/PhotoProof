"""Repair planning and execution.

Rules: SAFE repairs may run automatically; CONTROLLED repairs only when explicitly
requested; MANUAL findings are never automated. Every repair is re-verified by
re-running its check, and every applied repair emits ``REPAIR_APPLIED`` (audited).
"""

from contextlib import nullcontext
from dataclasses import dataclass
from functools import partial
from typing import TYPE_CHECKING

from photoproof.contracts.enums import Repairability, Severity
from photoproof.contracts.events import EventType
from photoproof.core.health.checks import CheckResult, HealthRegistry
from photoproof.core.storage.locking import file_lock

if TYPE_CHECKING:
    from photoproof.core.bootstrap import Application


@dataclass(frozen=True)
class RepairRecord:
    check_id: str
    category: str
    action: str
    outcome: str  # planned | applied | failed | skipped | manual
    changes: str = ""
    verified: bool | None = None

    def to_dict(self) -> dict[str, object]:
        return dict(vars(self))


def run_repairs(
    app: "Application",
    registry: HealthRegistry,
    *,
    allow_controlled: bool,
    dry_run: bool,
    results: list[CheckResult] | None = None,
    lock_timeout: float = 10.0,
) -> list[RepairRecord]:
    # Re-evaluate findings under the lock: a diagnostic snapshot can become obsolete.
    with (
        nullcontext()
        if dry_run
        else file_lock(app.storage.root, exclusive=True, timeout=lock_timeout)
    ):
        return _run_repairs(
            app,
            registry,
            allow_controlled=allow_controlled,
            dry_run=dry_run,
            results=results if dry_run else None,
        )


def _run_repairs(
    app: "Application",
    registry: HealthRegistry,
    *,
    allow_controlled: bool,
    dry_run: bool,
    results: list[CheckResult] | None,
) -> list[RepairRecord]:
    records: list[RepairRecord] = []
    for result in results if results is not None else registry.run_all(app):
        if result.ok or result.repair is None:
            continue
        action = result.repair
        record = partial(RepairRecord, result.check_id, action.category.value, action.description)
        if action.category is Repairability.MANUAL or action.apply is None:
            records.append(record(outcome="manual"))
        elif action.category is Repairability.CONTROLLED and not allow_controlled:
            records.append(record(outcome="skipped", changes="controlled repair requires --repair"))
        elif dry_run:
            records.append(record(outcome="planned"))
        else:
            try:
                changes = action.apply()
            except Exception as error:  # noqa: BLE001 - reported, never swallowed
                records.append(record(outcome="failed", changes=repr(error)))
                continue
            verified = registry.run(app, result.check_id).severity is Severity.OK
            applied = record(outcome="applied", changes=changes, verified=verified)
            records.append(applied)
            app.bus.emit(EventType.REPAIR_APPLIED, data=applied.to_dict())
    return records


@dataclass(frozen=True)
class PreflightReport:
    results: list[CheckResult]
    repairs: list[RepairRecord]
    blocking: list[CheckResult]

    @property
    def passed(self) -> bool:
        return not self.blocking


def preflight(app: "Application", registry: HealthRegistry) -> PreflightReport:
    """Run all checks, apply SAFE repairs if enabled, then re-check.

    Startup is blocked by any remaining finding at or above
    ``health.preflight_fail_on``. Findings are *not* published here: call
    ``publish_findings`` after ``Application.start()`` so they are persisted too.
    """
    results = registry.run_all(app)
    repairs: list[RepairRecord] = []
    if app.settings.recovery.auto_repair_safe and any(not r.ok for r in results):
        repairs = run_repairs(app, registry, allow_controlled=False, dry_run=False, results=results)
        results = registry.run_all(app)
    threshold = Severity(app.settings.health.preflight_fail_on).rank
    blocking = [r for r in results if r.severity.rank >= threshold]
    return PreflightReport(results, repairs, blocking)


def publish_findings(app: "Application", results: list[CheckResult]) -> None:
    """Emit ``HEALTH_CHECK_FAILED`` for every non-OK result (audited; persisted once the
    application has started)."""
    for result in results:
        if not result.ok:
            app.bus.emit(EventType.HEALTH_CHECK_FAILED, data=result.to_dict())
