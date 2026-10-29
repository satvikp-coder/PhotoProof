"""Trusted plugin discovery.

Only packages beneath a root package chosen *in code* (``photoproof.plugins`` in
production, a fixture package in tests) are inspected. Configuration can never add a
discovery location. Importing a package is not a sandbox: discovered plugins are
trusted code.
"""

import hashlib
import importlib
import pkgutil
from dataclasses import dataclass
from pathlib import Path

BUILTIN_PLUGIN_PACKAGE = "photoproof.plugins"


@dataclass(frozen=True)
class DiscoveredPlugin:
    package: str
    discovery_index: int
    raw_metadata: object  # validated later by the registry
    directory: Path | None
    import_error: str | None = None


def implementation_digest(directory: Path) -> str:
    """SHA-256 over the plugin package's ``*.py`` files (name + bytes), in sorted order."""
    digest = hashlib.sha256()
    for path in sorted(directory.glob("*.py")):
        digest.update(path.name.encode() + b"\0" + path.read_bytes() + b"\0")
    return digest.hexdigest()


def discover(root_package: str = BUILTIN_PLUGIN_PACKAGE) -> list[DiscoveredPlugin]:
    """Return every package under ``root_package`` that defines ``METADATA``.

    Order is the sorted dotted package name, which makes enumeration deterministic
    across platforms and filesystems. Packages without ``METADATA`` are treated as
    category namespaces (e.g. ``compression``).
    """
    root = importlib.import_module(root_package)
    failures: dict[str, str] = {}

    def on_error(name: str) -> None:
        failures[name] = "package could not be imported"

    names = sorted(
        name
        for _, name, is_package in pkgutil.walk_packages(
            root.__path__, prefix=f"{root.__name__}.", onerror=on_error
        )
        if is_package
    )
    found: list[DiscoveredPlugin] = []
    for name in sorted(set(names) | set(failures)):
        try:
            module = importlib.import_module(name)
        except Exception as error:  # noqa: BLE001 - a broken plugin must not break discovery
            found.append(DiscoveredPlugin(name, len(found), None, None, repr(error)))
            continue
        if hasattr(module, "METADATA"):
            directory = Path(module.__file__).parent if module.__file__ else None
            found.append(DiscoveredPlugin(name, len(found), module.METADATA, directory))
    return found
