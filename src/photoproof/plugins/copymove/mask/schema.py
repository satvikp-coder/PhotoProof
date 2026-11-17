"""Configuration schema for the copy-move mask plugin."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class Config(BaseModel):
    """Copy-move mask plugin configuration with validation."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    #: ZNCC correlation window size in pixels (must be odd).
    window_size: int = Field(default=11, ge=3, le=51)

    #: Minimum ZNCC correlation score to accept a match region.
    threshold: float = Field(default=0.5, ge=0.0, le=1.0)

    #: Minimum number of matched pairs to form a mask cluster.
    min_cluster_size: int = Field(default=5, ge=1, le=1000)
