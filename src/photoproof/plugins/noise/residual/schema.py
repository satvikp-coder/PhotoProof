"""Configuration schema for the noise residual plugin."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class Config(BaseModel):
    """Noise residual plugin configuration with validation."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    #: Wavelet name (PyWavelets wavelet name, e.g. 'db8', 'sym4', 'haar').
    wavelet: str = Field(default="db8")

    #: Number of wavelet decomposition levels (default 4).
    levels: int = Field(default=4, ge=1, le=6)

    #: Denoising method: 'bayesshrink' or 'wiener'.
    method: str = Field(default="bayesshrink", pattern=r"^(bayesshrink|wiener)$")

    #: Color channel: 'luma' or 'rgb'.
    channel: str = Field(default="luma", pattern=r"^(luma|rgb)$")
