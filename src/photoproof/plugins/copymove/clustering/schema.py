"""Configuration schema for the clustering plugin."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class Config(BaseModel):
    """Clustering plugin configuration with validation."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    #: Clustering method: "hierarchical" (Ward linkage) or "dbscan".
    method: str = Field(default="hierarchical", pattern=r"^(hierarchical|dbscan)$")

    #: Cut threshold for hierarchical clustering (default 0.5).
    cut_threshold: float = Field(default=0.5, ge=0.0, le=1.0)

    #: Eps parameter for DBSCAN clustering (default 0.5).
    eps: float = Field(default=0.5, ge=0.0, le=10.0)

    #: Minimum matches per cluster (default 4, since RANSAC needs 3).
    min_matches_per_cluster: int = Field(default=4, ge=1, le=100)
