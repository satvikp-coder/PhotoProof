"""Provisional descriptive difference thresholds, not manipulation probabilities."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


class Config(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, allow_inf_nan=False)

    threshold: Literal["otsu"] | float = "otsu"
    min_threshold: float = Field(default=0.15, ge=0, le=1)
    min_region_area: float = Field(default=0.0005, gt=0, le=1)
    kernel_size: int = Field(default=3, ge=1, le=9)
    max_regions: int = Field(default=256, ge=1, le=1000)
    max_pixels: int = Field(default=4194304, ge=1, le=4194304)

    @field_validator("threshold", mode="before")
    @classmethod
    def bounded_threshold(cls, value: object) -> object:
        if value == "otsu":
            return value
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not 0 <= value <= 1:
            raise ValueError("threshold must be otsu or a finite value in [0,1]")
        return value

    @field_validator("kernel_size")
    @classmethod
    def odd_kernel(cls, value: int) -> int:
        if value % 2 == 0:
            raise ValueError("kernel_size must be odd")
        return value
