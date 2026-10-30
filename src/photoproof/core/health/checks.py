"""Health checks: structured diagnostics, each with an optional repair action.

A check never changes anything. Repairs are separate callables attached to the
result and are executed only by ``health.repair``.
"""

import re
import shutil
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from functools import partial
from typing import TYPE_CHECKING

from photoproof.contracts.enums import Repairability, Severity
from photoproof.contracts.exceptions import DatabaseError, MigrationRequiredError, PlanningError
from photoproof.core.cache.run_cache import RunCache
from photoproof.core.database.store import LATEST_VERSION, Database
from photoproof.core.pipeline.planner import validate_graph
from photoproof.core.storage.store import BlobKind
from photoproof.versioning.build import distribution_version

if TYPE_CHECKING:
    from photoproof.core.bootstrap import Application

_SHA256 = re.compile(r"^[0-9a-f]{64}$")


@dataclass(frozen=True)
class RepairAction:
    description: str
    category: Repairability
    apply: Callable[[], str] | None = None  # returns a description of what changed


@dataclass(frozen=True)
class CheckResult:
    check_id: str
    severity: Severity
    message: str
    details: tuple[str, ...] = ()
    repair: RepairAction | None = None

    @property
    def ok(self) -> bool:
        return self.severity is Severity.OK

    def to_dict(self) -> dict[str, object]:
        return {
            "check_id": self.check_id,
            "severity": self.severity.value,
            "message": self.message,
            "details": list(self.details),
            "repair": None
            if self.repair is None
            else {
                "description": self.repair.description,
                "category": self.repair.category.value,
                "automatic": self.repair.apply is not None,
            },
        }


Check = Callable[["Application"], CheckResult]


@dataclass
class HealthRegistry:
    checks: dict[str, Check] = field(default_factory=dict)

    def register(self, check_id: str, check: Check) -> None:
        if check_id in self.checks:
            raise ValueError(f"health check {check_id!r} already registered")
        self.checks[check_id] = check

    def run(self, app: "Application", check_id: str) -> CheckResult:
        try:
            return self.checks[check_id](app)
        except Exception as error:  # noqa: BLE001 - a crashing check is itself a finding
            return CheckResult(check_id, Severity.CRITICAL, f"health check crashed: {error!r}")

    def run_all(self, app: "Application") -> list[CheckResult]:
        return [self.run(app, check_id) for check_id in self.checks]


def _ok(check_id: str, message: str) -> CheckResult:
    return CheckResult(check_id, Severity.OK, message)


# ------------------------------------------------------------------------ checks


def check_runtime_directories(app: "Application") -> CheckResult:
    missing = app.storage.missing_dirs()
    if not missing:
        return _ok("runtime.directories", "all runtime directories present")
    return CheckResult(
        "runtime.directories",
        Severity.ERROR,
        f"{len(missing)} runtime directories missing",
        tuple(missing),
        RepairAction(
            "create missing runtime directories",
            Repairability.SAFE,
            lambda: "created " + ", ".join(app.storage.ensure_layout()),
        ),
    )


def check_runtime_permissions(app: "Application") -> CheckResult:
    insecure = app.storage.insecure_dirs()
    if not insecure:
        return _ok("runtime.permissions", "runtime directories are owner-only")

    def restrict() -> str:
        for name in insecure:
            (app.storage.root / name).chmod(0o700)
        return "restricted to 0700: " + ", ".join(insecure)

    return CheckResult(
        "runtime.permissions",
        Severity.WARNING,
        "runtime directories accessible to other users",
        tuple(insecure),
        RepairAction("restrict permissions to owner-only", Repairability.SAFE, restrict),
    )


def check_disk_space(app: "Application") -> CheckResult:
    target = app.storage.root if app.storage.root.exists() else app.root
    free_mb = shutil.disk_usage(target).free // (1024 * 1024)
    minimum = app.settings.resources.min_free_disk_mb
    if free_mb >= minimum:
        return _ok("runtime.disk_space", f"{free_mb} MB free")
    return CheckResult(
        "runtime.disk_space",
        Severity.ERROR,
        f"only {free_mb} MB free; {minimum} MB required",
        repair=RepairAction("free disk space manually", Repairability.MANUAL),
    )


def check_stale_temp(app: "Application") -> CheckResult:
    stale = app.storage.stale_temp_files(app.settings.runtime.temp_max_age_hours)
    if not stale:
        return _ok("runtime.stale_temp", "no stale temporary files")
    return CheckResult(
        "runtime.stale_temp",
        Severity.WARNING,
        f"{len(stale)} stale temporary files",
        tuple(p.name for p in stale),
        RepairAction(
            "delete the listed stale temporary files",
            Repairability.SAFE,
            lambda: f"removed {len(app.storage.remove_temp_files(stale))} files",
        ),
    )


def check_cache_integrity(app: "Application", *, deep: bool = True) -> CheckResult:
    cache = RunCache(app.storage, None)
    broken = {
        key: problem
        for key in cache.entry_keys()
        if (problem := cache.problem(key, verify_artifacts=deep))
    }
    if not broken:
        return _ok("cache.integrity", f"{len(cache.entry_keys())} cache entries valid")

    def invalidate() -> str:
        db = app.database_or_none()
        for key, problem in broken.items():
            RunCache(app.storage, db).invalidate(key, problem)
        return f"invalidated {len(broken)} cache entries"

    return CheckResult(
        "cache.integrity",
        Severity.WARNING,
        f"{len(broken)} invalid cache entries",
        tuple(f"{k[:12]}…: {v}" for k, v in broken.items()),
        RepairAction("invalidate disposable cache entries", Repairability.SAFE, invalidate),
    )


def check_database(app: "Application") -> CheckResult:
    path = app.database_path
    if not path.parent.is_dir():
        # No own repair: the runtime.directories repair creates it.
        return CheckResult("database.integrity", Severity.ERROR, "database directory missing")
    if not path.is_file():  # checks never create anything; initializing is a SAFE repair
        return CheckResult(
            "database.integrity",
            Severity.WARNING,
            "database not initialized",
            repair=RepairAction(
                "initialize an empty database",
                Repairability.SAFE,
                lambda: f"initialized {app.database().path.name}",
            ),
        )
    try:
        db = app.database()
    except MigrationRequiredError as error:

        def migrate() -> str:
            Database.open(path, allow_migration=True).close()
            return f"backed up and migrated {path.name} to schema {LATEST_VERSION}"

        return CheckResult(
            "database.integrity",
            Severity.ERROR,
            str(error),
            repair=RepairAction(
                "back up and migrate the database", Repairability.CONTROLLED, migrate
            ),
        )
    except DatabaseError as error:
        return CheckResult(
            "database.integrity",
            Severity.CRITICAL,
            str(error),
            repair=RepairAction("inspect the database manually", Repairability.MANUAL),
        )
    problems = db.integrity_problems()
    if problems:
        return CheckResult(
            "database.integrity",
            Severity.CRITICAL,
            "SQLite integrity check failed",
            tuple(problems),
            RepairAction("restore from a verified backup", Repairability.MANUAL),
        )
    return _ok("database.integrity", f"schema version {db.schema_version()}, integrity ok")


def check_stored_blobs(app: "Application", *, deep: bool = True) -> CheckResult:
    """Deep: re-hash every blob (doctor/repair). Quick (preflight): only check that each
    file sits at a well-formed content address, so startup does not scale with storage."""
    corrupt: list[str] = []
    kinds: tuple[BlobKind, ...] = ("objects", "artifacts")
    for kind in kinds:
        for path in app.storage.iter_blobs(kind):
            well_formed = bool(_SHA256.match(path.name)) and path.name[:2] == path.parent.name
            if not well_formed or (deep and not app.storage.verify_blob(kind, path.name)):
                corrupt.append(f"{kind}/{path.parent.name}/{path.name}")
    corrupt += _missing_evidence(app)
    if not corrupt:
        return _ok("storage.integrity", "all stored blobs match their hashes")
    evidence = any(c.startswith("objects/") for c in corrupt)
    return CheckResult(
        "storage.integrity",
        Severity.CRITICAL if evidence else Severity.ERROR,
        f"{len(corrupt)} stored blobs fail hash verification",
        tuple(corrupt),
        RepairAction(
            "never modified automatically; investigate and restore from a verified copy",
            Repairability.MANUAL,
        ),
    )


def _missing_evidence(app: "Application") -> list[str]:
    """Originals the database knows about but ``objects/`` no longer holds."""
    if not app.database_path.is_file():  # doctor must not create the database as a side effect
        return []
    db = app.database_or_none()
    if db is None:
        return []  # reported by database.integrity
    try:
        hashes = db.evidence_hashes()
    except DatabaseError:
        return []  # reported by database.integrity
    return [
        f"objects/{sha256[:2]}/{sha256} (missing)"
        for sha256 in hashes
        if not (app.storage.root / "objects" / sha256[:2] / sha256).is_file()
    ]


def check_version_consistency(app: "Application") -> CheckResult:
    declared = app.identity.application.version
    installed = distribution_version(app.identity.application.package)
    if installed == declared:
        return _ok("application.version", f"installed version {installed} matches app.toml")
    return CheckResult(
        "application.version",
        Severity.ERROR,
        f"installed package version {installed} differs from app.toml version {declared}",
        repair=RepairAction(
            "reinstall the package: python main.py --repair", Repairability.CONTROLLED
        ),
    )


def check_plugins(app: "Application") -> CheckResult:
    try:
        validate_graph(app.registry)
    except PlanningError as error:
        return CheckResult(
            "plugins.graph",
            Severity.ERROR,
            str(error),
            repair=RepairAction("fix plugin metadata", Repairability.MANUAL),
        )
    if app.registry.unavailable:
        return CheckResult(
            "plugins.graph",
            Severity.WARNING,
            f"{len(app.registry.unavailable)} plugins unavailable",
            tuple(f"{u.plugin_id or u.package}: {u.reason}" for u in app.registry.unavailable),
            RepairAction("fix or remove the unavailable plugins", Repairability.MANUAL),
        )
    return _ok("plugins.graph", f"{len(app.registry)} plugins registered, graph valid")


STALE_RUN_AGE = timedelta(hours=1)


def check_stale_runs(app: "Application") -> CheckResult:
    """Runs that never finished (process killed or crashed before finalizing)."""
    db = app.database_or_none() if app.database_path.is_file() else None  # never create it
    if db is None:
        return _ok("database.stale_runs", "database unavailable; skipped")
    cutoff = (datetime.now(UTC) - STALE_RUN_AGE).isoformat()
    stale = db.unfinished_runs(older_than=cutoff)
    if not stale:
        return _ok("database.stale_runs", "no unfinished runs")
    ids = [str(run["run_id"]) for run in stale]

    def mark() -> str:
        db.mark_runs_aborted(ids)
        return f"marked {len(ids)} unfinished runs ABORTED"

    return CheckResult(
        "database.stale_runs",
        Severity.WARNING,
        f"{len(ids)} runs never finished (older than {STALE_RUN_AGE})",
        tuple(f"{run['run_id']} ({run['status']}, created {run['created_at']})" for run in stale),
        RepairAction("mark them ABORTED", Repairability.CONTROLLED, mark),
    )


def check_policy_references(app: "Application") -> CheckResult:
    """Policy entries naming plugins that do not exist are almost always typos — and a
    typo in a blacklist means the intended plugin is *not* blocked."""
    known = app.registry.known_ids
    unknown = [
        f"{where}: {pid}"
        for where, ids in (
            ("plugins.blacklist", app.settings.plugins.blacklist),
            ("security.forbidden_plugins", app.settings.security.forbidden_plugins),
            *(
                (f"profile {name}.required_plugins", profile.required_plugins)
                for name, profile in app.profiles.items()
            ),
        )
        for pid in ids
        if pid not in known
    ]
    if not unknown:
        return _ok("plugins.policy", "every policy entry names a known plugin")
    return CheckResult(
        "plugins.policy",
        Severity.WARNING,
        f"{len(unknown)} policy entries name unknown plugins (typo?)",
        tuple(unknown),
        RepairAction("correct or remove the entries", Repairability.MANUAL),
    )


def check_imaging_codecs(app: "Application") -> CheckResult:
    missing = app.decoders.missing(app.settings.formats.accepted)
    if not missing:
        return _ok("imaging.codecs", "every accepted format has a decoder")
    return CheckResult(
        "imaging.codecs",
        Severity.WARNING,
        f"{len(missing)} accepted formats have no decoder: {', '.join(sorted(missing))}",
        tuple(f"{name}: needs {package}" for name, package in sorted(missing.items())),
        RepairAction(
            "install what each format needs, or remove it from formats.accepted",
            Repairability.MANUAL,
        ),
    )


def default_registry(*, deep: bool = True) -> HealthRegistry:
    """``deep=False`` (preflight) skips re-hashing stored data; ``doctor`` runs deep."""
    registry = HealthRegistry()
    for check_id, check in (
        ("runtime.directories", check_runtime_directories),
        ("imaging.codecs", check_imaging_codecs),
        ("runtime.permissions", check_runtime_permissions),
        ("runtime.disk_space", check_disk_space),
        ("runtime.stale_temp", check_stale_temp),
        ("database.integrity", check_database),
        ("database.stale_runs", check_stale_runs),
        ("cache.integrity", partial(check_cache_integrity, deep=deep)),
        ("storage.integrity", partial(check_stored_blobs, deep=deep)),
        ("application.version", check_version_consistency),
        ("plugins.graph", check_plugins),
        ("plugins.policy", check_policy_references),
    ):
        registry.register(check_id, check)
    return registry
