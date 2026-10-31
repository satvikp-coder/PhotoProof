"""Construct the application's services from configuration.

``bootstrap`` reads and validates configuration and discovers plugins, but changes
nothing on disk. ``Application.start`` (called after preflight) attaches the services
that need runtime directories: file logging, the audit trail, and state persistence.
"""

import os
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

import photoproof
from photoproof.contracts.events import Event, EventType
from photoproof.contracts.exceptions import (
    ConfigurationError,
    DatabaseError,
    IdentityError,
    ReportError,
)
from photoproof.core.config.flags import FeatureFlags
from photoproof.core.config.loader import load_identity, load_profiles, load_settings
from photoproof.core.config.validator import AppIdentity, Profile, Settings
from photoproof.core.database.store import Database
from photoproof.core.events.bus import EventBus
from photoproof.core.ingestion import IngestionService
from photoproof.core.logging.setup import (
    attach_event_logging,
    configure_logging,
    shutdown_logging,
)
from photoproof.core.pipeline.discovery import BUILTIN_PLUGIN_PACKAGE, discover
from photoproof.core.pipeline.registry import PluginRegistry
from photoproof.core.reporting.service import generate_report, validate_run_id
from photoproof.core.storage.evidence import EvidenceStore
from photoproof.core.storage.store import RuntimeStorage
from photoproof.imaging.decoder import DecodeLimits
from photoproof.imaging.registry import DecoderRegistry, default_decoders

# Default repository root for a source checkout: src/photoproof/core/bootstrap.py -> repo
DEFAULT_ROOT = Path(__file__).resolve().parents[3]


@dataclass
class Application:
    root: Path
    identity: AppIdentity
    settings: Settings
    profiles: Mapping[str, Profile]
    registry: PluginRegistry
    flags: FeatureFlags
    storage: RuntimeStorage
    bus: EventBus
    decoders: DecoderRegistry = field(default_factory=default_decoders)
    _db: Database | None = field(default=None, repr=False)
    started: bool = False

    @property
    def database_path(self) -> Path:
        return self.storage.root / "database" / f"{self.identity.application.package}.db"

    def database(self) -> Database:
        """Open (and initialize if new) the database. Raises ``DatabaseError``."""
        if self._db is None:
            self._db = Database.open(self.database_path)
        return self._db

    def database_or_none(self) -> Database | None:
        try:
            return self.database() if self.database_path.parent.is_dir() else None
        except DatabaseError:
            return None

    def evidence(self) -> EvidenceStore:
        """The verifying store for original evidence. Raises ``DatabaseError``."""
        return EvidenceStore(
            self.storage,
            self.database(),
            max_input_bytes=self.settings.resources.max_input_bytes,
            accepted_formats=self.settings.formats.accepted,
        )

    def ingestion(self) -> IngestionService:
        """The only way untrusted files enter PhotoProof. Raises ``DatabaseError``."""
        return IngestionService(
            self.storage,
            self.evidence(),
            self.bus,
            max_input_bytes=self.settings.resources.max_input_bytes,
            accepted_formats=self.settings.formats.accepted,
        )

    def decode_limits(self) -> DecodeLimits:
        resources = self.settings.resources
        return DecodeLimits(
            max_input_bytes=resources.max_input_bytes, max_pixels=resources.max_image_pixels
        )

    def report(self, run_id: str, *, format: Literal["html", "json"]) -> Path:
        """Publish a report only from integrity-verified stored evidence."""
        validate_run_id(run_id)
        if not self.database_path.is_file():
            raise ReportError("no runtime database; analyze an input before generating a report")
        try:
            database = self.database()
        except DatabaseError as error:
            raise ReportError(
                "run database unavailable or needs migration; run doctor before reporting"
            ) from error
        return generate_report(self.storage, database, run_id, format=format)

    def start(self) -> None:
        """Attach runtime services. Requires the runtime layout (see preflight)."""
        if self.started:
            return
        log_dir = self.storage.root / "logs"
        configure_logging(self.settings.logging, log_dir)
        attach_persistence(self.bus, self.database())
        self.started = True
        self.bus.emit(
            EventType.APPLICATION_STARTED, data={"version": self.identity.application.version}
        )

    def stop(self) -> None:
        if self.started:
            self.bus.emit(EventType.APPLICATION_STOPPED)
        if self._db is not None:
            self._db.close()
            self._db = None
        shutdown_logging()


def validate_package_layout(identity: AppIdentity) -> None:
    """The imported package must be ``src/<application.package>/`` (``src`` is a layout
    directory, not part of the import name)."""
    package_dir = Path(photoproof.__file__).resolve().parent
    expected = identity.application.package
    if package_dir.name != expected or package_dir.parent.name != "src":
        raise IdentityError(
            f"package directory {package_dir} does not correspond to src/{expected}/; "
            "install PhotoProof in editable mode from the repository (python main.py)"
        )


def bootstrap(
    root: Path = DEFAULT_ROOT,
    *,
    plugin_package: str = BUILTIN_PLUGIN_PACKAGE,
    environ: Mapping[str, str] | None = None,
) -> Application:
    identity = load_identity(root)
    validate_package_layout(identity)
    settings = load_settings(root, identity, os.environ if environ is None else environ)
    profiles = load_profiles(root)
    if settings.profiles.default not in profiles:
        raise ConfigurationError(
            f"profiles.default {settings.profiles.default!r} has no file in configs/profiles/"
        )
    configure_logging(settings.logging, None)
    bus = EventBus()
    storage = RuntimeStorage(root / settings.runtime.root)
    attach_event_logging(bus, storage.root / "logs")

    registry = PluginRegistry(
        discover(plugin_package), host_api=identity.application.plugin_api_version
    )
    flags = FeatureFlags.build(
        settings.features.flags,
        plugin_ids=registry.known_ids,
        unknown=settings.features.unknown_flags,
    )
    for plugin in registry:
        bus.emit(
            EventType.PLUGIN_DISCOVERED,
            plugin_id=plugin.id,
            data={"version": plugin.metadata.version, "package": plugin.package},
        )
    return Application(root, identity, settings, profiles, registry, flags, storage, bus)


def attach_persistence(bus: EventBus, db: Database) -> None:
    """Persist run and plugin state from events. Critical: failures abort the run."""

    def persist(event: Event) -> None:
        data = event.data
        if event.type is EventType.RUN_CREATED and event.run_id:
            db.create_run(
                event.run_id,
                mode=str(data["mode"]),
                profile=str(data["profile"]),
                cache_key=_opt(data.get("cache_key")),
            )
        elif event.type is EventType.RUN_STARTED and event.run_id:
            db.set_run_status(event.run_id, "RUNNING")
        elif event.type in {EventType.RUN_COMPLETED, EventType.RUN_ABORTED} and event.run_id:
            db.finish_run(
                event.run_id,
                status=str(data["status"]),
                cache_hit=bool(data.get("cache_hit")),
                manifest_sha256=_opt(data.get("manifest_sha256")),
            )
        elif event.type is EventType.ARTIFACT_CREATED and event.run_id and event.plugin_id:
            db.record_artifact(
                run_id=event.run_id,
                plugin_id=event.plugin_id,
                sha256=str(data["sha256"]),
                name=str(data["name"]),
                media_type=str(data["media_type"]),
                size=int(str(data["size"])),
            )
        elif event.run_id and event.plugin_id and "state" in data:
            db.record_plugin_state(
                event.run_id,
                event.plugin_id,
                state=str(data["state"]),
                failure_category=_opt(data.get("failure_category")),
                message=_opt(data.get("message")),
                started_at=_opt(data.get("started_at")),
                finished_at=_opt(data.get("finished_at")),
            )
        elif event.type is EventType.HEALTH_CHECK_FAILED:
            db.record_health_check(
                str(data["check_id"]), str(data["severity"]), str(data["message"])
            )

    bus.subscribe(persist, critical=True, name="state-persistence")


def _opt(value: object) -> str | None:
    return None if value is None else str(value)
