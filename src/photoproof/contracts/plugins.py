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

import numpy as np
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from photoproof.contracts.artifacts import ArtifactRef
from photoproof.contracts.exceptions import PolicyError

_SEMVER = r"^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)$"
_ID = r"^[a-z][a-z0-9_]*(\.[a-z][a-z0-9_]*)+$"  # e.g. "compression.ela"
_CONTRACT = r"^[a-z][a-z0-9_]*(\.[a-z][a-z0-9_]*)+$"  # e.g. "input.target", "noise.residual_map"
_ENTRY = r"^[a-z_][a-z0-9_]*:[A-Za-z_][A-Za-z0-9_]*$"  # module-in-package:attribute

#: Contracts a run provides. ``input.target``/``input.reference`` descriptors come from
#: the request; the media contracts (PP-012) are resolved once per run from stored
#: evidence: ``input.original_bytes`` is the stored original, ``image.decoded`` the
#: pixels as decoded, and ``image.working`` the normalized working image, both handed
#: to declaring plugins as read-only NumPy arrays. A run that cannot resolve them skips
#: the plugins that declared them; missing pixels are never clean evidence.
RUN_INPUTS = frozenset(
    {"input.target", "input.reference", "input.original_bytes", "image.decoded", "image.working"}
)


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
    optional_inputs: tuple[str, ...] = ()  # contracts read if present; never required
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

    @field_validator("inputs", "outputs", "optional_inputs")
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
        declared = set(self.inputs) | set(self.optional_inputs)
        if RUN_INPUTS & set(self.outputs):
            raise ValueError("plugins cannot produce reserved run inputs")
        if declared & set(self.outputs):
            raise ValueError("plugins cannot consume contracts they produce")
        return self


ArtifactWriter = Callable[[str, bytes, str], ArtifactRef]


@dataclass(frozen=True)
class PluginContext:
    """Everything a plugin may use. Plugins get no direct filesystem access: they
    persist binary output only through ``write_artifact``.

    Plugin context provides access to the run's declared inputs through
    :attr:`inputs`. In addition, :meth:`image` and :meth:`original_bytes` provide
    convenient access to image data and original bytes respectively.
    """

    run_id: str
    plugin_id: str
    inputs: Mapping[str, object]  # only the contracts the plugin declared
    config: object  # validated config model instance, or None
    seed: int
    write_artifact: ArtifactWriter  # (name, data, media_type) -> ArtifactRef
    logger: logging.Logger

    def image(self, name: str) -> np.ndarray:
        """Return the decoded image array for the given input contract.

        The contract must be one declared in the plugin's ``inputs`` metadata,
        typically ``image.decoded`` or ``image.working``. The returned array is
        read-only; any attempt to mutate it raises ``ValueError``.
        """
        if name not in self.inputs:
            raise PolicyError(f"plugin {self.plugin_id} did not declare input {name!r}")
        value = self.inputs[name]
        if not isinstance(value, np.ndarray):
            raise PolicyError(f"input {name!r} is not a NumPy array (got {type(value).__name__})")
        if value.flags.writeable:
            value = value.copy()
            value.flags.writeable = False
        return value

    def original_bytes(self) -> bytes:
        """Return the original input bytes.

        The ``input.original_bytes`` contract must be declared in the plugin's
        ``inputs`` metadata.
        """
        if "input.original_bytes" not in self.inputs:
            raise PolicyError(
                f"plugin {self.plugin_id} did not declare input 'input.original_bytes'"
            )
        value = self.inputs["input.original_bytes"]
        if not isinstance(value, (bytes, bytearray)):
            raise PolicyError(
                f"input 'input.original_bytes' is not bytes (got {type(value).__name__})"
            )
        return bytes(value)


class PluginEntryPoint(Protocol):
    def __call__(self, ctx: PluginContext) -> Mapping[str, object]: ...
