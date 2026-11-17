"""Configuration schema for the SIFT keypoint plugin."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class Config(BaseModel):
    """SIFT keypoint plugin configuration with validation."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    #: Maximum number of keypoints to retain (default 20000).
    max_keypoints: int = Field(default=20000, ge=1, le=100000)

    #: Contrast threshold for keypoint detection (default 0.04).
    contrast_threshold: float = Field(default=0.04, ge=0.0, le=1.0)

    #: Edge threshold for keypoint detection (default 10.0).
    edge_threshold: float = Field(default=10.0, ge=0.0, le=100.0)

    #: Use RootSIFT normalization (default True).
    rootsift: bool = Field(default=True)

    #: Max edge length for resizing before detection (default 3000).
    max_edge: int = Field(default=3000, ge=1, le=100000)
