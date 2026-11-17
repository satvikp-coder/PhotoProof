"""Bounded provisional configuration; calibration is tracked in #37."""

from pydantic import BaseModel, ConfigDict, Field, model_validator


class Config(BaseModel):
    """Thresholds are heuristic scores, never probabilities."""

    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)
    high: float = Field(default=0.70, gt=0, le=1)
    low: float = Field(default=0.30, gt=0, le=1)
    kernel_size: int = Field(default=3, ge=1, le=21)
    min_area_fraction: float = Field(default=0.001, ge=0, le=1)
    polygon_tolerance: float = Field(default=1.0, ge=0, le=100)
    max_regions: int = Field(default=50, ge=1, le=100)

    @model_validator(mode="after")
    def _ordered(self) -> "Config":
        if self.low > self.high:
            raise ValueError("low must be <= high; lower the growth threshold")
        if self.kernel_size % 2 == 0:
            raise ValueError("kernel_size must be odd; choose 1, 3, ..., 21")
        return self
