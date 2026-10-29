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
    """SHA-256 over every file in the plugin package (relative path + bytes), sorted.

    Covers subpackages and data files (tables, weights), so any change to what the
    plugin ships invalidates cached results. Bytecode caches are excluded.
    """
    digest = hashlib.sha256()
    files = (
        p
        for p in directory.rglob("*")
        if p.is_file() and "__pycache__" not in p.parts and p.suffix not in {".pyc", ".pyo"}
    )
    for path in sorted(files, key=lambda p: p.relative_to(directory).as_posix()):
        relative = path.relative_to(directory).as_posix()
        digest.update(relative.encode() + b"\0" + path.read_bytes() + b"\0")
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
