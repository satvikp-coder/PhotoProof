"""Plugin registry: validated metadata, availability and lazy implementation loading."""

import importlib
import importlib.util
from collections import Counter
from collections.abc import Iterable, Iterator
from dataclasses import dataclass
from pathlib import Path

from pydantic import BaseModel, ValidationError

from photoproof.contracts.exceptions import ConfigurationError, PluginLoadError, RegistryError
from photoproof.contracts.plugins import PluginEntryPoint, PluginMetadata
from photoproof.core.config.loader import load_plugin_config
from photoproof.core.pipeline.discovery import DiscoveredPlugin, implementation_digest
from photoproof.versioning.compatibility import plugin_api_compatible


@dataclass(frozen=True)
class RegisteredPlugin:
    metadata: PluginMetadata
    package: str
    discovery_index: int
    implementation_sha256: str

    @property
    def id(self) -> str:
        return self.metadata.id

    def _resolve(self, reference: str) -> object:
        module_name, attribute = reference.split(":")
        try:
            module = importlib.import_module(f"{self.package}.{module_name}")
            return getattr(module, attribute)
        except (ImportError, AttributeError) as error:
            raise PluginLoadError(f"{self.id}: cannot load {reference!r}: {error}") from error

    def load_entry_point(self) -> PluginEntryPoint:
        """Import the implementation on first use (metadata loading never imports it)."""
        entry = self._resolve(self.metadata.entry_point)
        if not callable(entry):
            raise PluginLoadError(
                f"{self.id}: entry point {self.metadata.entry_point} is not callable"
            )
        return entry

    def load_config(self, root: Path) -> BaseModel | None:
        """Validate ``configs/plugins/<id>.yaml`` against the plugin's schema."""
        raw = load_plugin_config(root, self.id)
        if self.metadata.config_schema is None:
            if raw is not None:
                raise ConfigurationError(
                    f"{self.id}: has a config file but declares no config_schema"
                )
            return None
        model = self._resolve(self.metadata.config_schema)
        if not (isinstance(model, type) and issubclass(model, BaseModel)):
            raise PluginLoadError(f"{self.id}: config_schema must name a pydantic model")
        try:
            return model.model_validate(raw or {})
        except ValidationError as error:
            raise ConfigurationError(f"{self.id}: invalid plugin configuration: {error}") from error


@dataclass(frozen=True)
class UnavailablePlugin:
    package: str
    plugin_id: str | None
    reason: str


class PluginRegistry:
    def __init__(self, discovered: Iterable[DiscoveredPlugin], *, host_api: str) -> None:
        self._plugins: dict[str, RegisteredPlugin] = {}
        self.unavailable: list[UnavailablePlugin] = []
        entries = list(discovered)

        claimed = Counter(_raw_id(entry.raw_metadata) for entry in entries)
        duplicates = sorted(pid for pid, count in claimed.items() if pid and count > 1)
        if duplicates:
            raise RegistryError(f"duplicate plugin identifiers: {', '.join(duplicates)}")

        for entry in entries:
            problem, metadata = self._validate(entry, host_api)
            if problem or metadata is None or entry.directory is None:
                self.unavailable.append(
                    UnavailablePlugin(
                        entry.package, _raw_id(entry.raw_metadata), problem or "invalid"
                    )
                )
                continue
            self._plugins[metadata.id] = RegisteredPlugin(
                metadata,
                entry.package,
                entry.discovery_index,
                implementation_digest(entry.directory),
            )

    @staticmethod
    def _validate(
        entry: DiscoveredPlugin, host_api: str
    ) -> tuple[str | None, PluginMetadata | None]:
        if entry.import_error:
            return f"import failed: {entry.import_error}", None
        try:
            metadata = PluginMetadata.model_validate(entry.raw_metadata)
        except ValidationError as error:
            fields = "; ".join(
                f"{'.'.join(map(str, e['loc'])) or '<root>'}: {e['msg']}" for e in error.errors()
            )
            return f"invalid metadata: {fields}", None
        if not plugin_api_compatible(metadata.api_version, host_api):
            return (
                f"plugin API {metadata.api_version} is incompatible with host API {host_api}",
                None,
            )
        for reference in filter(None, (metadata.entry_point, metadata.config_schema)):
            module = f"{entry.package}.{reference.split(':')[0]}"
            try:
                spec = importlib.util.find_spec(module)
            except (ImportError, ValueError):
                spec = None
            if spec is None:
                return f"entry module {module} not found", None
        return None, metadata

    def __iter__(self) -> Iterator[RegisteredPlugin]:
        return iter(self._plugins.values())

    def __len__(self) -> int:
        return len(self._plugins)

    def __contains__(self, plugin_id: object) -> bool:
        return plugin_id in self._plugins

    def get(self, plugin_id: str) -> RegisteredPlugin:
        return self._plugins[plugin_id]

    @property
    def known_ids(self) -> frozenset[str]:
        """Available plus unavailable-but-identified plugins."""
        return frozenset(self._plugins) | {u.plugin_id for u in self.unavailable if u.plugin_id}


def _raw_id(raw: object) -> str | None:
    value = raw.get("id") if isinstance(raw, dict) else None
    return value if isinstance(value, str) else None
