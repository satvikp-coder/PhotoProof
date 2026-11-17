"""Configuration schema for the verification plugin."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class Config(BaseModel):
    """Verification plugin configuration with validation."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    #: Geometric model: ``affine`` (6 DOF) or ``similarity`` (4 DOF).
    model: str = Field(default="similarity", pattern=r"^(affine|similarity)$")

    #: Reprojection threshold in pixels for inlier counting.
    reproj_threshold: float = Field(default=3.0, ge=0.5, le=100.0)

    #: Minimum inliers to accept a cluster.
    min_inliers: int = Field(default=6, ge=3, le=1000)

    #: Maximum RANSAC iterations.
    max_iterations: int = Field(default=1000, ge=10, le=100000)

    #: Scale range for similarity model: min and max scale factor.
    scale_min: float = Field(default=0.5, ge=0.1, le=10.0)
    scale_max: float = Field(default=2.0, ge=0.1, le=10.0)

    #: Max shear angle in degrees for affine model.
    max_shear_deg: float = Field(default=45.0, ge=0.0, le=90.0)
