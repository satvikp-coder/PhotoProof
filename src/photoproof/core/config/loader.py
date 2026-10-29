"""Read ``app.toml``, ``settings.yaml``, profiles and plugin configs into validated models.

YAML is parsed with ruamel.yaml's safe, pure-Python loader, which also rejects
duplicate keys. No configuration
value is ever evaluated, imported or executed.
"""

import os
import tomllib
from collections.abc import Mapping
from pathlib import Path
from typing import TypeVar

from pydantic import BaseModel, ValidationError
from ruamel.yaml import YAML
from ruamel.yaml.error import YAMLError

from photoproof.contracts.exceptions import ConfigurationError, IdentityError
from photoproof.core.config.validator import AppIdentity, Profile, Settings
from photoproof.versioning.compatibility import config_schema_compatible

M = TypeVar("M", bound=BaseModel)

# Bootstrap paths: fixed relative to the repository root because they are needed
# before any configuration exists. Everything else is derived from settings.
APP_FILE = "app.toml"
SETTINGS_FILE = "settings.yaml"
PROFILES_DIR = Path("configs/profiles")
PLUGIN_CONFIG_DIR = Path("configs/plugins")

# The only environment variables allowed to override settings. Anything else that
# starts with PHOTOPROOF_ is rejected so typos do not silently do nothing.
ENV_OVERRIDES: Mapping[str, tuple[str, str]] = {
    "PHOTOPROOF_ENVIRONMENT": ("environment", "mode"),
    "PHOTOPROOF_LOG_LEVEL": ("logging", "level"),
}
LAUNCHER_ENV = frozenset({"PHOTOPROOF_LAUNCHER_VERBOSE"})  # read by main.py, ignored here


def _yaml() -> YAML:
    # "safe" never constructs arbitrary objects; "pure" avoids the optional C extension so
    # behaviour is identical on every Python version. Duplicate keys raise by default.
    return YAML(typ="safe", pure=True)


def _format_validation(source: Path, error: ValidationError) -> str:
    lines = [f"{source}: invalid configuration"]
    for item in error.errors():
        location = ".".join(str(part) for part in item["loc"]) or "<root>"
        lines.append(f"  - {location}: {item['msg']}")
    return "\n".join(lines)


def _validate(model: type[M], data: object, source: Path) -> M:
    try:
        return model.model_validate(data)
    except ValidationError as error:
        raise ConfigurationError(_format_validation(source, error)) from error


def read_yaml(path: Path) -> object:
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError as error:
        raise ConfigurationError(f"{path}: file not found") from error
    try:
        return _yaml().load(text)
    except YAMLError as error:
        raise ConfigurationError(f"{path}: malformed YAML: {error}") from error


def load_identity(root: Path) -> AppIdentity:
    path = root / APP_FILE
    try:
        data = tomllib.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as error:
        raise IdentityError(f"{path}: file not found") from error
    except tomllib.TOMLDecodeError as error:
        raise IdentityError(f"{path}: malformed TOML: {error}") from error
    try:
        return AppIdentity.model_validate(data)
    except ValidationError as error:
        raise IdentityError(_format_validation(path, error)) from error


def load_settings(
    root: Path, identity: AppIdentity, environ: Mapping[str, str] | None = None
) -> Settings:
    path = root / SETTINGS_FILE
    data = read_yaml(path)
    if not isinstance(data, dict):
        raise ConfigurationError(f"{path}: top level must be a mapping")
    environ = os.environ if environ is None else environ
    for name, value in environ.items():
        if not name.startswith("PHOTOPROOF_") or name in LAUNCHER_ENV:
            continue
        if name not in ENV_OVERRIDES:
            allowed = ", ".join(sorted(ENV_OVERRIDES))
            raise ConfigurationError(f"unsupported environment override {name}; allowed: {allowed}")
        section, key = ENV_OVERRIDES[name]
        if not isinstance(data.get(section), dict):
            raise ConfigurationError(f"{path}: section {section!r} missing; cannot apply {name}")
        data[section][key] = value
    settings = _validate(Settings, data, path)
    expected = identity.application.config_schema_version
    if not config_schema_compatible(settings.schema_version, expected):
        raise ConfigurationError(
            f"{path}: schema_version {settings.schema_version} is not supported; this release "
            f"expects {expected}. Migrate the file as described in docs/configuration/settings.md."
        )
    return settings


def load_profiles(root: Path) -> dict[str, Profile]:
    directory = root / PROFILES_DIR
    profiles: dict[str, Profile] = {}
    for path in sorted(directory.glob("*.yaml")):
        profile = _validate(Profile, read_yaml(path), path)
        if profile.name != path.stem:
            raise ConfigurationError(f"{path}: name {profile.name!r} must match the file name")
        profiles[profile.name] = profile
    if not profiles:
        raise ConfigurationError(f"{directory}: no analysis profiles found")
    return profiles


def load_plugin_config(root: Path, plugin_id: str) -> object:
    """Raw plugin configuration, or ``None`` if the plugin has no config file."""
    path = root / PLUGIN_CONFIG_DIR / f"{plugin_id}.yaml"
    return read_yaml(path) if path.is_file() else None
