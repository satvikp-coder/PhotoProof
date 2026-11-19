"""Configuration schema for the clustering plugin."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class Config(BaseModel):
    """Clustering plugin configuration with validation."""

    model_config = ConfigDict(extra="forbid", frozen=True, allow_inf_nan=False)

    #: Clustering method: "hierarchical" (Ward linkage) or "dbscan".
    method: Literal["hierarchical", "dbscan"] = "hierarchical"

    #: Dimensionless Ward cut in normalized source/target/displacement space.
    cut_threshold: float = Field(default=0.5, ge=0.0, le=1.0, strict=True)

    #: Displacement DBSCAN radius in working pixels.
    eps: float = Field(default=0.5, ge=1e-6, le=1000.0, strict=True)

    #: Conservative all-member affine coherence screen; not robust verification.
    coherence_rmse_px: float = Field(default=3.0, ge=1e-6, le=1000.0, strict=True)

    #: Join spatially adjacent fragments only when their combined fit stays coherent.
    merge_gap_px: float = Field(default=16.0, ge=0.0, le=256.0, strict=True)

    #: Minimum matches per cluster (default 4, since RANSAC needs 3).
    min_matches_per_cluster: int = Field(default=4, ge=1, le=100, strict=True)

    #: Bound the quadratic Ward distance matrix; larger inputs use density clustering.
    ward_limit: int = Field(default=512, ge=2, le=1024, strict=True)
    max_input_pairs: int = Field(default=50_000, ge=1, le=50_000, strict=True)
    max_neighbor_work: int = Field(default=5_000_000, ge=1, le=10_000_000, strict=True)
