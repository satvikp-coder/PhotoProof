"""Health checks: structured diagnostics, each with an optional repair action.

A check never changes anything. Repairs are separate callables attached to the
result and are executed only by ``health.repair``.
"""

import shutil
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from photoproof.contracts.enums import Repairability, Severity
from photoproof.contracts.exceptions import DatabaseError, PlanningError
from photoproof.core.cache.run_cache import RunCache
from photoproof.core.database.store import LATEST_VERSION, Database
from photoproof.core.pipeline.planner import validate_graph
from photoproof.core.storage.store import BlobKind
from photoproof.versioning.build import distribution_version

if TYPE_CHECKING:
    from photoproof.core.bootstrap import Application


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


def check_cache_integrity(app: "Application") -> CheckResult:
    cache = RunCache(app.storage, None)
    broken = {key: problem for key in cache.entry_keys() if (problem := cache.problem(key))}
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
    except DatabaseError as error:
        if "needs migration" in str(error):

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


def check_stored_blobs(app: "Application") -> CheckResult:
    corrupt: list[str] = []
    kinds: tuple[BlobKind, ...] = ("objects", "artifacts")
    for kind in kinds:
        for path in app.storage.iter_blobs(kind):
            if not app.storage.verify_blob(kind, path.name):
                corrupt.append(f"{kind}/{path.parent.name}/{path.name}")
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


def default_registry() -> HealthRegistry:
    registry = HealthRegistry()
    for check_id, check in (
        ("runtime.directories", check_runtime_directories),
        ("runtime.permissions", check_runtime_permissions),
        ("runtime.disk_space", check_disk_space),
        ("runtime.stale_temp", check_stale_temp),
        ("database.integrity", check_database),
        ("cache.integrity", check_cache_integrity),
        ("storage.integrity", check_stored_blobs),
        ("application.version", check_version_consistency),
        ("plugins.graph", check_plugins),
    ):
        registry.register(check_id, check)
    return registry
