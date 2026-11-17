"""Bound the display resolution independently of decoded input dimensions."""

from pydantic import BaseModel, ConfigDict, Field


class Config(BaseModel):
    """Maximum preview edge in pixels."""

    model_config = ConfigDict(extra="forbid")
    max_edge: int = Field(default=2048, ge=1, le=4096)
