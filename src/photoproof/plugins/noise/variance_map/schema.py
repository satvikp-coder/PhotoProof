"""Configuration schema for the noise variance map plugin."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class Config(BaseModel):
    """Noise variance map plugin configuration with validation."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    #: Block size for local variance estimation (default 32).
    block: int = Field(default=32, ge=8, le=128)

    #: Number of intensity bins for signal-dependent correction (default 8).
    intensity_bins: int = Field(default=8, ge=2, le=32)

    #: Texture threshold: blocks with gradient magnitude above this are excluded
    #    from noise estimation (default 15).
    texture_threshold: float = Field(default=15.0, ge=0.0)

    #: Saturation threshold: pixels above this fraction of max are considered
    #    saturated (default 0.98).
    saturation_threshold: float = Field(default=0.98, ge=0.0, le=1.0)

    #: Minimum valid block fraction for low-confidence flag (default 0.30).
    min_valid_fraction: float = Field(default=0.30, ge=0.0, le=1.0)
