"""Configuration schema for the sharpness plugin."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class Config(BaseModel):
    """Sharpness plugin configuration with validation."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    #: Block size in pixels for local sharpness computation.
    block: int = Field(default=32, ge=8, le=256)

    #: Whether to normalize by local contrast (divide by intensity variance + epsilon).
    contrast_normalize: bool = Field(default=True)
