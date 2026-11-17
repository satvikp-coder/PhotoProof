"""Configuration schema for the JPEG grid plugin."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class Config(BaseModel):
    """Grid detection plugin configuration with validation."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    #: Window size for local grid analysis (default 64, must be multiple of 8).
    window: int = Field(default=64, ge=8, le=256)

    #: Stride between consecutive windows (default 32).
    stride: int = Field(default=32, ge=8, le=128)
