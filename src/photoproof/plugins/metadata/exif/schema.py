"""Configuration schema for the metadata.exif plugin."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class Config(BaseModel):
    """Plugin configuration with validation."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    #: Maximum EXIF IFD nesting depth to prevent pathological recursion.
    max_ifd_depth: int = Field(default=10, ge=1, le=100)

    #: Hamming distance threshold for perceptual-hash thumbnail comparison.
    #: Below this, the thumbnail is considered consistent with the main image.
    thumbnail_hamming_threshold: int = Field(default=8, ge=0, le=64)

    #: Maximum EXIF block size in bytes to process; larger blocks are skipped.
    max_exif_bytes: int = Field(default=1 << 20, ge=4096, le=1 << 22)

    #: Include software/editor tags in the summary.
    include_software: bool = Field(default=True)
