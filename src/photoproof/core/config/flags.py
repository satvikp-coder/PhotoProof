"""Typed boolean feature flags.

Flags are plain booleans looked up by identifier. They never execute code. Two kinds
exist: application flags declared in ``KNOWN_FLAGS`` and per-plugin enable flags of the
form ``plugin.<plugin_id>``, which are known once the plugin is registered.
"""

import logging
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from types import MappingProxyType

from photoproof.contracts.exceptions import ConfigurationError

PLUGIN_PREFIX = "plugin."

# Application-level flags and their defaults. Add new flags here with a docs entry.
KNOWN_FLAGS: Mapping[str, bool] = MappingProxyType(
    {
        "cache.run_level": True,  # reuse validated results of identical runs
        "events.progress": False,  # print plugin progress events to stderr in the CLI
    }
)

_log = logging.getLogger(__name__)


@dataclass(frozen=True)
class FeatureFlags:
    """Effective flag state. ``overrides`` holds only explicitly configured values."""

    overrides: Mapping[str, bool]
    plugin_ids: frozenset[str] = field(default_factory=frozenset)

    @classmethod
    def build(
        cls, configured: Mapping[str, bool], *, plugin_ids: Iterable[str], unknown: str
    ) -> "FeatureFlags":
        ids = frozenset(plugin_ids)
        flags = cls(MappingProxyType(dict(configured)), ids)
        unknown_names = sorted(name for name in configured if not flags.is_known(name))
        if unknown_names:
            message = f"unknown feature flags: {', '.join(unknown_names)}"
            if unknown == "error":
                raise ConfigurationError(message + " (set features.unknown_flags: warn to allow)")
            _log.warning(message)
        return flags

    def is_known(self, name: str) -> bool:
        if name in KNOWN_FLAGS:
            return True
        return name.startswith(PLUGIN_PREFIX) and name[len(PLUGIN_PREFIX) :] in self.plugin_ids

    def is_enabled(self, name: str) -> bool:
        """State of a known application flag. Raises for unknown or plugin flags."""
        if name not in KNOWN_FLAGS:
            raise KeyError(f"{name!r} is not an application feature flag")
        return self.overrides.get(name, KNOWN_FLAGS[name])

    def plugin_override(self, plugin_id: str) -> bool | None:
        """Explicit enable/disable for a plugin, or ``None`` when the flag is not set."""
        return self.overrides.get(PLUGIN_PREFIX + plugin_id)

    def snapshot(self) -> dict[str, bool]:
        """Every effective value (defaults plus overrides), for manifests and inspection."""
        state = dict(KNOWN_FLAGS)
        state.update(self.overrides)
        return dict(sorted(state.items()))
