"""Configuration schema for the verification plugin."""

from __future__ import annotations

from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator


class Config(BaseModel):
    """Verification plugin configuration with validation."""

    model_config = ConfigDict(extra="forbid", frozen=True, allow_inf_nan=False)

    #: Geometric model: ``affine`` (6 DOF) or ``similarity`` (4 DOF).
    model: Literal["affine", "similarity"] = "similarity"

    #: Reprojection threshold in pixels for inlier counting.
    reproj_threshold: float = Field(default=3.0, ge=0.5, le=100.0, strict=True)

    #: Minimum distinct oriented-coordinate inliers to accept a cluster.
    min_inliers: int = Field(default=6, ge=3, le=1000, strict=True)

    #: Maximum RANSAC iterations.
    max_iterations: int = Field(default=1000, ge=10, le=100000, strict=True)

    #: Scale range for similarity model: min and max scale factor.
    scale_min: float = Field(default=0.5, ge=0.1, le=10.0, strict=True)
    scale_max: float = Field(default=2.0, ge=0.1, le=10.0, strict=True)

    #: Max shear angle in degrees for affine model.
    max_shear_deg: float = Field(default=45.0, ge=0.0, le=90.0, strict=True)

    # OpenCV sampling termination control, never a forgery probability.
    ransac_stop_confidence: float = Field(default=0.99, gt=0, lt=1, strict=True)
    periodic_min_corr: float = Field(default=0.85, ge=0.5, le=1, strict=True)
    periodic_max_edge: int = Field(default=128, ge=16, le=256, strict=True)
    periodic_max_lag: int = Field(default=32, ge=4, le=64, strict=True)
    max_ransac_work: int = Field(default=50_000_000, ge=1, le=100_000_000, strict=True)
    max_texture_work: int = Field(default=64_000_000, ge=1, le=256_000_000, strict=True)

    @model_validator(mode="before")
    @classmethod
    def _range_alias(cls, value: object) -> object:
        if not isinstance(value, dict) or "scale_range" not in value:
            return value
        if "scale_min" in value or "scale_max" in value:
            raise ValueError("use scale_range or scale_min/scale_max, not both")
        bounds = value["scale_range"]
        if not isinstance(bounds, (list, tuple)) or len(bounds) != 2:
            raise ValueError("scale_range requires [minimum,maximum]")
        return {
            **{k: v for k, v in value.items() if k != "scale_range"},
            "scale_min": bounds[0],
            "scale_max": bounds[1],
        }

    @model_validator(mode="after")
    def _ordered_range(self) -> Self:
        if self.scale_min > self.scale_max:
            raise ValueError("scale_min must not exceed scale_max; correct the verification range")
        return self
