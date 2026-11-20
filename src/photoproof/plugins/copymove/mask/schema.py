"""Explicit bounded dense localization controls; thresholds are uncalibrated."""

from typing import Self

from pydantic import BaseModel, ConfigDict, Field, model_validator


class Config(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, allow_inf_nan=False)

    zncc_window: int = Field(default=7, ge=3, le=51, strict=True)
    zncc_threshold: float = Field(default=0.9, ge=-1, le=1, strict=True)
    dilate_hull_px: int = Field(default=32, ge=0, le=256, strict=True)
    morphology_kernel: int = Field(default=3, ge=1, le=15, strict=True)
    min_displacement_px: float = Field(default=10.0, ge=0, le=1024, strict=True)
    max_pixels: int = Field(default=4_000_000, ge=1, le=4_000_000, strict=True)
    max_pixel_work: int = Field(default=32_000_000, ge=1, le=64_000_000, strict=True)

    @model_validator(mode="after")
    def _odd_windows(self) -> Self:
        if self.zncc_window % 2 != 1 or self.morphology_kernel % 2 != 1:
            raise ValueError("ZNCC and morphology windows must be odd")
        return self
