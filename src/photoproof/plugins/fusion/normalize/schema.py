"""Configuration schema for fusion.normalize.

No install-time configuration fields are required: normalization parameters live in
``METADATA.DETECTOR_NORMALIZATION``. This schema exists to satisfy the plugin
``config_schema`` contract and accept an empty config object.
"""

from __future__ import annotations

from pydantic import BaseModel


class Config(BaseModel):
    """Empty config; all normalization parameters are documented in METADATA."""

    model_config = {"extra": "forbid"}
