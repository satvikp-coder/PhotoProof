"""Bounded controls for descriptive image comparison, not forgery probabilities."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class Config(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, allow_inf_nan=False)

    sigma: float = Field(default=1.5, ge=0.5, le=5)
    multiscale: bool = False
    photometric_normalization: Literal["none", "gain_offset", "histogram"] = "none"
    edge_erosion: int = Field(default=1, ge=0, le=2)
    max_pixels: int = Field(default=4194304, ge=121, le=4194304)
