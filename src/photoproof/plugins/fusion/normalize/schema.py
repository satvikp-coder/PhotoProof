"""Validated per-detector normalization overrides; no executable configuration."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from photoproof.plugins.fusion.normalize import DETECTOR_NORMALIZATION, NATIVE_OUTPUTS


class Mapping(BaseModel):
    """Optional numeric/strength overrides for a known detector's defaults."""

    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)
    method: Literal["robust_z", "identity"] | None = None
    z0: float | None = Field(default=None, ge=0, strict=True)
    k: float | None = Field(default=None, gt=0, strict=True)
    strength_class: Literal["weak", "medium", "strong"] | None = None
    minimum_geometric_support: float | None = Field(default=None, ge=0.8, le=1.0, strict=True)


class Config(BaseModel):
    """Explicit mappings only; absent keys use the versioned documented defaults."""

    model_config = ConfigDict(extra="forbid")
    mappings: dict[str, Mapping] = Field(default_factory=dict)

    @field_validator("mappings")
    @classmethod
    def _known(cls, value: dict[str, Mapping]) -> dict[str, Mapping]:
        unknown = set(value) - set(NATIVE_OUTPUTS)
        if unknown:
            raise ValueError(f"unknown detector mappings: {sorted(unknown)}")
        ranks = {"weak": 0, "medium": 1, "strong": 2}
        for name, mapping in value.items():
            expected = "identity" if name == "copymove.mask" else "robust_z"
            if mapping.method is not None and mapping.method != expected:
                raise ValueError(f"{name}: native mapping requires {expected}")
            if mapping.minimum_geometric_support is not None and name != "copymove.mask":
                raise ValueError("minimum_geometric_support applies only to copy-move masks")
            if name == "copymove.mask" and (mapping.z0 is not None or mapping.k is not None):
                raise ValueError("copy-move mask similarity does not use robust-z parameters")
            default = str(DETECTOR_NORMALIZATION[name]["strength_class"])
            if (
                mapping.strength_class is not None
                and ranks[mapping.strength_class] > ranks[default]
            ):
                raise ValueError(
                    f"{name}: configuration cannot promote the documented {default} class"
                )
        return value
