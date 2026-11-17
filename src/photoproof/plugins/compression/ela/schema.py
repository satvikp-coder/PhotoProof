"""Configuration schema for the ELA plugin."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class Config(BaseModel):
    """ELA plugin configuration with validation."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    #: JPEG quality for recompression (1-100, default 95).
    quality: int = Field(default=95, ge=1, le=100)

    #: Channel aggregation method: "max" or "mean".
    aggregate: str = Field(default="max", pattern=r"^(max|mean)$")

    #: Block size for anomaly map (default 16).
    block: int = Field(default=16, ge=2)

    #: Divide error by local gradient energy to reduce texture false positives.
    edge_suppression: bool = Field(default=True)
