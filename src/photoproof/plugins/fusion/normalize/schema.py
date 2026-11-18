"""Validated per-detector normalization overrides; no executable configuration."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from photoproof.plugins.fusion.normalize import DETECTOR_NORMALIZATION


class Mapping(BaseModel):
    """Optional numeric/strength overrides for a known detector's defaults."""

    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)
    method: Literal["robust_z", "identity"] | None = None
    z0: float | None = None
    k: float | None = Field(default=None, gt=0)
    strength_class: Literal["weak", "medium", "strong"] | None = None


class Config(BaseModel):
    """Explicit mappings only; absent keys use the versioned documented defaults."""

    model_config = ConfigDict(extra="forbid")
    mappings: dict[str, Mapping] = Field(default_factory=dict)

    @field_validator("mappings")
    @classmethod
    def _known(cls, value: dict[str, Mapping]) -> dict[str, Mapping]:
        unknown = set(value) - DETECTOR_NORMALIZATION.keys()
        if unknown:
            raise ValueError(f"unknown detector mappings: {sorted(unknown)}")
        ranks = {"weak": 0, "medium": 1, "strong": 2}
        for name, mapping in value.items():
            default = str(DETECTOR_NORMALIZATION[name]["strength_class"])
            if (
                mapping.strength_class is not None
                and ranks[mapping.strength_class] > ranks[default]
            ):
                raise ValueError(
                    f"{name}: configuration cannot promote the documented {default} class"
                )
        return value
