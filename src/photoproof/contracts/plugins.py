"""Plugin metadata and execution-context contracts (plugin API 1.0.0).

A plugin is a Python package that exposes ``METADATA`` (a plain ``dict``) in its
``__init__.py`` and implements ``run(ctx: PluginContext) -> Mapping[str, object]`` in the
module named by ``entry_point``. Plugins import only from ``photoproof.contracts``.
"""

import logging
import re
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Protocol

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from photoproof.contracts.artifacts import ArtifactRef

_SEMVER = r"^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)$"
_ID = r"^[a-z][a-z0-9_]*(\.[a-z][a-z0-9_]*)+$"  # e.g. "compression.ela"
_CONTRACT = r"^[a-z][a-z0-9_]*(\.[a-z][a-z0-9_]*)+$"  # e.g. "input.target", "noise.residual_map"
_ENTRY = r"^[a-z_][a-z0-9_]*:[A-Za-z_][A-Za-z0-9_]*$"  # module-in-package:attribute

RUN_INPUTS = frozenset({"input.target", "input.reference"})


class PluginMetadata(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    id: str = Field(pattern=_ID, max_length=100)
    name: str = Field(min_length=1, max_length=100)
    description: str = Field(min_length=1)
    version: str = Field(pattern=_SEMVER)
    api_version: str = Field(pattern=_SEMVER)
    priority: int = Field(ge=0)
    required: bool = False
    enabled_default: bool = True
    requires: tuple[str, ...] = ()  # must finish SUCCESS before this plugin runs
    after: tuple[str, ...] = ()  # must only reach a terminal state first
    entry_point: str = Field(default="processor:run", pattern=_ENTRY)
    inputs: tuple[str, ...] = ()
    outputs: tuple[str, ...] = Field(min_length=1)
    timeout_seconds: float | None = Field(default=None, gt=0)
    supported_formats: tuple[str, ...] = ()  # empty = format-independent
    deterministic: bool = True
    config_schema: str | None = Field(default=None, pattern=_ENTRY)  # e.g. "schema:Config"
    python_dependencies: tuple[str, ...] = ()  # distributions whose versions affect results

    @field_validator("requires", "after")
    @classmethod
    def _ids(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if len(set(value)) != len(value):
            raise ValueError("duplicate plugin identifiers")
        return value

    @field_validator("inputs", "outputs")
    @classmethod
    def _contracts(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        for name in value:
            if not re.match(_CONTRACT, name):
                raise ValueError(f"invalid contract name {name!r}")
        if len(set(value)) != len(value):
            raise ValueError("duplicate contract names")
        return value

    @model_validator(mode="after")
    def _relations(self) -> "PluginMetadata":
        if self.id in self.requires or self.id in self.after:
            raise ValueError("a plugin cannot depend on itself")
        if set(self.requires) & set(self.after):
            raise ValueError("a dependency cannot be both `requires` and `after`")
        if RUN_INPUTS & set(self.outputs):
            raise ValueError("plugins cannot produce reserved run inputs")
        return self


ArtifactWriter = Callable[[str, bytes, str], ArtifactRef]


@dataclass(frozen=True)
class PluginContext:
    """Everything a plugin may use. Plugins get no direct filesystem access: they
    persist binary output only through ``write_artifact``."""

    run_id: str
    plugin_id: str
    inputs: Mapping[str, object]  # only the contracts the plugin declared
    config: object  # validated config model instance, or None
    seed: int
    write_artifact: ArtifactWriter  # (name, data, media_type) -> ArtifactRef
    logger: logging.Logger


class PluginEntryPoint(Protocol):
    def __call__(self, ctx: PluginContext) -> Mapping[str, object]: ...
