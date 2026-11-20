"""Bounded geometric registration controls; these are not calibrated forensic thresholds."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class Config(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, allow_inf_nan=False)

    model: Literal["similarity", "affine", "homography"] = "homography"
    ratio: float = Field(default=0.75, gt=0, lt=1)
    ransac_threshold: float = Field(default=3.0, gt=0, le=10)
    max_iterations: int = Field(default=2000, ge=1, le=10000)
    min_inliers: int = Field(default=12, ge=6, le=1000)
    min_inlier_ratio: float = Field(default=0.25, gt=0, le=1)
    max_rmse: float = Field(default=2.0, gt=0, le=10)
    max_condition: float = Field(default=1000, ge=1, le=10000)
    min_support_fraction: float = Field(default=0.005, gt=0, le=0.5)
    max_features: int = Field(default=4000, ge=16, le=8000)
    max_edge: int = Field(default=1600, ge=64, le=2048)
    max_pixels: int = Field(default=4194304, ge=256, le=4194304)
    contrast_threshold: float = Field(default=0.04, gt=0, le=1)
    edge_threshold: float = Field(default=10, gt=0, le=100)
    rootsift: bool = True
    affine_fallback: bool = True
    allow_reflection: bool = False
    refine_ecc: bool = False
    ecc_iterations: int = Field(default=50, ge=1, le=100)
