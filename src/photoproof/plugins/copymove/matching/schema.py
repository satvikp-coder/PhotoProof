"""Configuration schema for the SIFT descriptor matching plugin."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class Config(BaseModel):
    """SIFT descriptor matching plugin configuration with validation."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    #: Number of nearest neighbours to retrieve (default 10 for g2NN).
    k: int = Field(default=10, ge=2, le=50)

    #: Generalized Lowe ratio threshold (default 0.5 as in Amerini et al.).
    ratio: float = Field(default=0.5, ge=0.0, le=1.0)

    #: Minimum spatial distance between matched keypoints (default 10 px).
    min_spatial_distance: float = Field(default=10.0, ge=0.0, le=1000.0)

    #: Maximum number of matches to retain (strongest first, default 50000).
    max_pairs: int = Field(default=50000, ge=1, le=1000000)
