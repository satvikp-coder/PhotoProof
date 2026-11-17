"""Configuration schema for the edges plugin."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class Config(BaseModel):
    """Edges plugin configuration with validation."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    #: Canny edge detection sigma for automatic threshold computation.
    sigma: float = Field(default=1.0, ge=0.1, le=5.0)

    #: Profile length in pixels for edge width sampling.
    profile_length: int = Field(default=15, ge=5, le=100)

    #: Minimum segment length in pixels for contour segments.
    min_segment_length: int = Field(default=20, ge=5, le=1000)
