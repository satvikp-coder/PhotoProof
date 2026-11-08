"""Configuration schema for fusion.rules.

Rules and weights live here, validated by pydantic. A rule change is a plugin
MINOR or MAJOR bump.
"""

from __future__ import annotations

from pydantic import BaseModel, Field, field_validator


class StrengthWeights(BaseModel):
    """Weight per strength class in the weighted noisy-OR."""

    strong: float = Field(default=1.0, ge=0.0, le=1.0)
    medium: float = Field(default=0.6, ge=0.0, le=1.0)
    weak: float = Field(default=0.3, ge=0.0, le=1.0)


class Config(BaseModel):
    """Fusion rules configuration."""

    strength_weights: StrengthWeights = Field(default_factory=StrengthWeights)
    medium_high_score_threshold: float = Field(
        default=0.8,
        ge=0.0,
        le=1.0,
        description="A medium layer scoring above this can mark a region "
        "suspicious without support from another family.",
    )
    weak_boost_factor: float = Field(
        default=0.2,
        ge=0.0,
        le=1.0,
        description="Factor by which weak layers raise confidence where "
        "medium/strong evidence already exists.",
    )
    weak_only_max_score: float = Field(
        default=0.4,
        ge=0.0,
        le=1.0,
        description="Maximum score a weak-only combination can reach. Weak "
        "evidence alone must never reach 'suspicious' (>= 0.5).",
    )

    @field_validator("medium_high_score_threshold", "weak_boost_factor", "weak_only_max_score")
    @classmethod
    def _in_unit_interval(cls, v: float) -> float:
        if not 0.0 <= v <= 1.0:
            raise ValueError(f"value {v} must be in [0, 1]")
        return v
