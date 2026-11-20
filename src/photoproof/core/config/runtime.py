"""Trusted packaged defaults and explicit runtime-root selection for installed wheels."""

import os
import sys
from collections.abc import Mapping
from importlib.resources import files
from pathlib import Path

from photoproof.contracts.exceptions import ConfigurationError
from photoproof.core.security.paths import safe_join


def packaged_defaults() -> Path | None:
    """Return build-packaged data, never infer code locations from configuration."""
    resource = files("photoproof").joinpath("_defaults")
    if not resource.is_dir():
        return None  # Source checkouts use the original files, not duplicated defaults.
    if not isinstance(resource, Path):
        raise ConfigurationError("install the PhotoProof wheel unpacked into site-packages")
    return resource


def configuration_path(root: Path, relative: str) -> Path:
    """Prefer an explicit user file; otherwise use the fixed packaged default."""
    defaults = packaged_defaults()
    if relative == "app.toml" and defaults is not None:
        return safe_join(defaults, relative)  # Installed identity belongs to the distribution.
    local = safe_join(root, relative)
    literal = root / relative  # Containment was validated above; inspect a dangling link itself.
    if literal.is_symlink() and not local.exists():
        raise ConfigurationError(f"{literal}: configured link target is missing; fix the link")
    if local.exists():
        return local
    return safe_join(defaults, relative) if defaults is not None else local


def resolve_root(
    root: Path | None,
    *,
    checkout_root: Path,
    environ: Mapping[str, str],
) -> Path:
    """Explicit root wins; HOME override is allowlisted and must be absolute."""
    if root is not None:
        return root.expanduser().resolve()
    home = environ.get("PHOTOPROOF_HOME")
    if home is not None:
        if not home.strip() or not Path(home).expanduser().is_absolute():
            raise ConfigurationError("PHOTOPROOF_HOME must be a nonempty absolute path")
        return Path(home).expanduser().resolve()
    if packaged_defaults() is None:
        return checkout_root
    if sys.platform == "win32":
        return Path.home() / "AppData" / "Local" / "PhotoProof"
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / "PhotoProof"
    return Path.home() / ".local" / "share" / "photoproof"


def initialize_configuration(root: Path, *, checkout_root: Path) -> tuple[str, ...]:
    """Write editable defaults, converting filesystem errors to actionable configuration errors."""
    try:
        return _initialize_configuration(root, checkout_root=checkout_root)
    except OSError as error:
        raise ConfigurationError(
            f"cannot initialize configuration in {root}: {error}; check directory permissions"
        ) from error


def _initialize_configuration(root: Path, *, checkout_root: Path) -> tuple[str, ...]:
    """Write missing editable defaults exclusively; preserve every existing path."""
    defaults = packaged_defaults()
    source = defaults or checkout_root
    names = ["settings.yaml"] if defaults is not None else ["app.toml", "settings.yaml"]
    config = safe_join(source, "configs")
    names.extend(path.relative_to(source).as_posix() for path in sorted(config.rglob("*.yaml")))
    created = []
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    for name in names:
        safe_join(root, name)  # Validate containment before retaining the lexical filename.
        target = root / name  # O_EXCL must see the link itself, not its resolved destination.
        # Even a dangling link is existing operator data: never follow or replace it.
        if target.exists() or target.is_symlink():
            continue
        data = safe_join(source, name).read_bytes()
        target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        try:
            descriptor = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        except FileExistsError:
            continue  # Another initializer won; do not overwrite its file.
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(data)
        created.append(name)
    return tuple(created)
