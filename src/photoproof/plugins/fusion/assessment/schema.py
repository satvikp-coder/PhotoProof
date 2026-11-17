"""Configuration schema for fusion.assessment.

Coverage rules for determining sufficient evidence.
"""

from __future__ import annotations

from pydantic import BaseModel, Field, field_validator


class Config(BaseModel):
    """Assessment configuration."""

    minimum_families_for_sufficient_coverage: int = Field(
        default=2,
        ge=1,
        description="Minimum number of independent detector families with "
        "non-low confidence required for sufficient coverage.",
    )
    manipulation_confidence_threshold: float = Field(
        default=0.5,
        ge=0.0,
        le=1.0,
        description="Minimum fused suspicion score to indicate manipulation.",
    )

    @field_validator("manipulation_confidence_threshold")
    @classmethod
    def _in_unit_interval(cls, v: float) -> float:
        if not 0.0 <= v <= 1.0:
            raise ValueError(f"value {v} must be in [0, 1]")
        return v
