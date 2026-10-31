"""Typed configuration models. Unknown keys are rejected everywhere (``extra="forbid"``)."""

import re
from ipaddress import ip_address
from pathlib import PurePosixPath
from typing import Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StrictBool,
    StrictInt,
    field_validator,
    model_validator,
)

from photoproof.contracts.enums import AnalysisMode, Severity
from photoproof.versioning.version import SemVer

_PLUGIN_ID = r"^[a-z][a-z0-9_]*(\.[a-z][a-z0-9_]*)+$"


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


def _plugin_ids(values: tuple[str, ...], what: str) -> tuple[str, ...]:
    bad = [v for v in values if not re.match(_PLUGIN_ID, v)]
    if bad:
        raise ValueError(f"invalid plugin identifiers in {what}: {', '.join(bad)}")
    return _unique(values, what)


def _unique(values: tuple[str, ...], what: str) -> tuple[str, ...]:
    duplicates = sorted({v for v in values if values.count(v) > 1})
    if duplicates:
        raise ValueError(f"duplicate {what}: {', '.join(duplicates)}")
    return values


# --------------------------------------------------------------------------- app.toml


class Developer(_Strict):
    name: str = Field(min_length=1)
    github: str = ""


class ApplicationSection(_Strict):
    name: str = Field(min_length=1)
    version: str
    package: str = Field(pattern=r"^[a-z_][a-z0-9_]*$")
    description: str
    release_channel: Literal["development", "preview", "stable"]
    plugin_api_version: str
    config_schema_version: int = Field(ge=1)

    @field_validator("version", "plugin_api_version")
    @classmethod
    def _semver(cls, value: str) -> str:
        SemVer.parse(value)
        return value


class PythonSection(_Strict):
    minimum: str = Field(pattern=r"^3\.\d+$")
    maximum: str = Field(pattern=r"^3\.\d+$")


class PlatformSection(_Strict):
    operating_systems: tuple[Literal["Linux", "Darwin", "Windows"], ...] = Field(min_length=1)
    architectures: tuple[Literal["x86_64", "arm64"], ...] = Field(min_length=1)


class HardwareSection(_Strict):
    min_cpu_cores: int = Field(ge=1)
    recommended_cpu_cores: int = Field(ge=1)
    min_memory_mb: int = Field(ge=0)
    recommended_memory_mb: int = Field(ge=0)
    min_free_disk_mb: int = Field(ge=0)
    gpu: Literal["none", "optional", "required"]


class AppIdentity(_Strict):
    application: ApplicationSection
    developers: tuple[Developer, ...] = Field(min_length=4, max_length=4)
    python: PythonSection
    platform: PlatformSection
    hardware: HardwareSection

    @model_validator(mode="after")
    def _identity(self) -> "AppIdentity":
        app = self.application
        if app.name.lower() != app.package:
            raise ValueError(
                f"application.name.lower() ({app.name.lower()!r}) must equal "
                f"application.package ({app.package!r})"
            )
        return self


# ---------------------------------------------------------------------- settings.yaml


class EnvironmentSettings(_Strict):
    mode: Literal["development", "testing", "production"]


class RuntimeSettings(_Strict):
    root: str
    temp_max_age_hours: int = Field(ge=1)

    @field_validator("root")
    @classmethod
    def _relative(cls, value: str) -> str:
        path = PurePosixPath(value.replace("\\", "/"))
        if path.is_absolute() or ".." in path.parts or not path.parts:
            raise ValueError("runtime.root must be a relative path inside the repository")
        return value


class SchedulingSettings(_Strict):
    executor: Literal["sequential"]
    strict_priority_barriers: StrictBool
    default_plugin_timeout_seconds: float = Field(gt=0, le=86_400)
    random_seed: int = Field(ge=0)


class DependencySettings(_Strict):
    on_unavailable_requirement: Literal["skip", "block"]


class FailurePolicySettings(_Strict):
    on_required_failure: Literal["abort", "degrade"]


class FeatureSettings(_Strict):
    unknown_flags: Literal["error", "warn"]
    flags: dict[str, StrictBool]


class PluginSettings(_Strict):
    blacklist: tuple[str, ...]
    allow_profile_overrides: StrictBool

    @field_validator("blacklist")
    @classmethod
    def _ids(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        return _plugin_ids(value, "blacklist entries")


class ProfileSettings(_Strict):
    default: str = Field(pattern=r"^[a-z][a-z0-9_-]*$")


class CacheSettings(_Strict):
    enabled: StrictBool


class LoggingSettings(_Strict):
    level: Literal["DEBUG", "INFO", "WARNING", "ERROR"]
    console_format: Literal["text", "json"]
    file_max_bytes: int = Field(ge=10_000)
    file_backups: int = Field(ge=0, le=50)


class HealthSettings(_Strict):
    preflight_fail_on: Literal[Severity.WARNING, Severity.ERROR, Severity.CRITICAL]


class RecoverySettings(_Strict):
    auto_repair_safe: StrictBool


class ResourceSettings(_Strict):
    max_input_bytes: int = Field(ge=1)
    max_image_pixels: int = Field(ge=1)
    min_free_disk_mb: int = Field(ge=0)


class SecuritySettings(_Strict):
    forbidden_plugins: tuple[str, ...]
    allow_blacklist_bypass: StrictBool

    @field_validator("forbidden_plugins")
    @classmethod
    def _ids(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        return _plugin_ids(value, "forbidden plugin entries")


class FormatSettings(_Strict):
    accepted: tuple[Literal["jpeg", "png", "webp", "heif", "avif", "jxl", "tiff"], ...]

    @field_validator("accepted")
    @classmethod
    def _formats(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        return _unique(value, "formats")


class ReportingSettings(_Strict):
    include_effective_settings: StrictBool


class ServiceSettings(_Strict):
    background_workers: int = Field(ge=0, le=0)  # ponytail: workers not implemented yet (backlog)


def is_loopback_host(host: str) -> bool:
    """One definition for configuration and HTTP binding; never resolve DNS names."""
    try:
        return host == "localhost" or ip_address(host).is_loopback
    except ValueError:
        return False


class UISettings(_Strict):
    host: str = "127.0.0.1"
    port: StrictInt = Field(default=8501, ge=1, le=65_535)

    @field_validator("host")
    @classmethod
    def _host(cls, value: str) -> str:
        if value == "localhost":
            return value
        try:
            parsed = ip_address(value)
        except ValueError as error:
            raise ValueError(
                "ui.host must be an IP address or localhost, without a port"
            ) from error
        if "%" in value:
            raise ValueError("ui.host must not contain an IPv6 scope identifier")
        if not is_loopback_host(str(parsed)):
            raise ValueError("ui.host must be loopback until WebKit authentication exists")
        return str(parsed)


class DevelopmentSettings(_Strict):
    debug: StrictBool


class Settings(_Strict):
    schema_version: int = Field(ge=1)
    environment: EnvironmentSettings
    runtime: RuntimeSettings
    scheduling: SchedulingSettings
    dependencies: DependencySettings
    failure_policy: FailurePolicySettings
    features: FeatureSettings
    plugins: PluginSettings
    profiles: ProfileSettings
    cache: CacheSettings
    logging: LoggingSettings
    health: HealthSettings
    recovery: RecoverySettings
    resources: ResourceSettings
    security: SecuritySettings
    formats: FormatSettings
    reporting: ReportingSettings
    services: ServiceSettings
    ui: UISettings
    development: DevelopmentSettings

    @model_validator(mode="after")
    def _conflicts(self) -> "Settings":
        production = self.environment.mode == "production"
        problems = []
        if production and self.development.debug:
            problems.append("development.debug must be false in production")
        if production and self.security.allow_blacklist_bypass:
            problems.append("security.allow_blacklist_bypass must be false in production")
        if not is_loopback_host(self.ui.host):
            problems.append("ui.host must be a loopback address until WebKit authentication exists")
        overlap = set(self.plugins.blacklist) & set(self.security.forbidden_plugins)
        if overlap:
            problems.append(
                f"plugins listed in both plugins.blacklist and security.forbidden_plugins: "
                f"{', '.join(sorted(overlap))} (keep them in forbidden_plugins only)"
            )
        if problems:
            raise ValueError("; ".join(problems))
        return self


# ---------------------------------------------------------------- configs/profiles/*.yaml


class ProfilePluginOverrides(_Strict):
    enable: tuple[str, ...] = ()
    disable: tuple[str, ...] = ()

    @model_validator(mode="after")
    def _disjoint(self) -> "ProfilePluginOverrides":
        _unique(self.enable, "profile enable entries")
        _unique(self.disable, "profile disable entries")
        both = set(self.enable) & set(self.disable)
        if both:
            raise ValueError(f"plugins both enabled and disabled: {', '.join(sorted(both))}")
        return self


class Profile(_Strict):
    name: str = Field(pattern=r"^[a-z][a-z0-9_-]*$")
    description: str
    mode: AnalysisMode
    plugins: ProfilePluginOverrides = ProfilePluginOverrides()
    required_plugins: tuple[str, ...] = ()
    required_outputs: tuple[str, ...] = ()

    @model_validator(mode="after")
    def _consistent(self) -> "Profile":
        _plugin_ids(self.plugins.enable, "plugins.enable")
        _plugin_ids(self.plugins.disable, "plugins.disable")
        _plugin_ids(self.required_plugins, "required_plugins")
        contradictory = set(self.required_plugins) & set(self.plugins.disable)
        if contradictory:
            raise ValueError(
                f"plugins both required and disabled: {', '.join(sorted(contradictory))}"
            )
        return self
