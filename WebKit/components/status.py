"""Read-only, explicitly redacted projections of existing core services."""

import asyncio
from datetime import UTC, datetime
from time import monotonic

from starlette.concurrency import run_in_threadpool

from photoproof.core.bootstrap import Application
from photoproof.core.config.policy import OverrideOrigin, decide_all
from photoproof.core.health.checks import default_registry

_LABELS = {
    "runtime.directories": "Runtime directories",
    "runtime.permissions": "Runtime permissions",
    "runtime.disk_space": "Available disk space",
    "runtime.stale_temp": "Temporary-file housekeeping",
    "database.integrity": "Database integrity",
    "database.stale_runs": "Unfinished runs",
    "cache.integrity": "Cache structure",
    "storage.integrity": "Evidence storage structure",
    "application.version": "Installed application version",
    "plugins.graph": "Plugin dependency graph",
    "plugins.policy": "Plugin policy references",
    "imaging.codecs": "Image decoder availability",
    "isolation.capabilities": "Process isolation capabilities",
}


def status_context(services: Application) -> dict[str, object]:
    """Inspect with thread-owned, read-only database services; never borrow live SQLite."""
    with services.diagnostic_view() as app:
        return _collect(app)


def _collect(app: Application) -> dict[str, object]:
    """Return allowlisted display fields, never raw settings, paths or error details.

    Quick checks inspect structure, not evidence hashes. Repairs are never run from
    requests. All policy calls explicitly identify the WEB boundary with no override.
    """
    results = default_registry(deep=False).run_all(app)
    decisions = decide_all(
        (plugin.metadata for plugin in app.registry),
        settings=app.settings,
        flags=app.flags,
        profile=app.profiles[app.settings.profiles.default],
        override=None,
        origin=OverrideOrigin.WEB,
    )
    return {
        "health_summary": (
            "Unavailable"
            if not results
            else "Checks passed"
            if all(r.ok for r in results)
            else "Attention required"
        ),
        "health_rows": [
            {"label": _LABELS.get(r.check_id, "Additional check"), "severity": r.severity.value}
            for r in results
        ],
        "plugins": [
            {
                "id": plugin.id,
                "name": plugin.metadata.name,
                "version": plugin.metadata.version,
                "enabled": decisions[plugin.id].enabled,
                "source": decisions[plugin.id].source.value,
            }
            for plugin in app.registry
        ],
        "unavailable_plugins": [
            plugin.plugin_id or "Unidentified plugin" for plugin in app.registry.unavailable
        ],
        "checked_at": datetime.now(UTC).strftime("%H:%M:%S UTC"),
    }


class StatusCache:
    """One bounded-age snapshot per application and at most one in-flight collection."""

    def __init__(self) -> None:
        self._value: dict[str, object] | None = None
        self._expires = 0.0
        self._lock = asyncio.Lock()

    async def get(self, app: Application) -> dict[str, object]:
        """Run filesystem work off the event loop, with fresh read-only core services."""
        async with self._lock:
            if self._value is None or monotonic() >= self._expires:
                self._value = await run_in_threadpool(status_context, app)
                self._expires = monotonic() + 30
            return self._value
