"""Configuration schema for the JPEG DCT plugin."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class Config(BaseModel):
    """JPEG DCT plugin configuration with validation."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    #: Number of low-frequency AC coefficients to use for double-compression
    #    histogram analysis (1-10, default 5).
    num_ac_frequencies: int = Field(default=5, ge=1, le=10)

    #: EM iterations for mixture modelling (1-50, default 10).
    em_iterations: int = Field(default=10, ge=1, le=50)

    #: Minimum number of 8x8 blocks required for confident analysis (default 10).
    min_blocks: int = Field(default=10, ge=1, le=1000)
