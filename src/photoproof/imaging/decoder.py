"""Decoder security interface that every image decoder must implement.

This module fixes the contract so ingestion, plugins and the
future isolated decoder process agree on limits and outputs. Implementing this
protocol does not by itself provide isolation: decoders parse untrusted bytes with
native code and must eventually run in a separate, resource-limited process.
"""

from dataclasses import dataclass
from typing import Protocol

# Pixel modes holding 16-bit little-endian code values, with their channel counts. Sources of
# 10 or 12 bits use the same storage, and ``DecodedImage.bit_depth`` says which range applies.
SIXTEEN_BIT_MODES = {"I;16": 1, "RGB;16": 3, "RGBA;16": 4}


@dataclass(frozen=True)
class DecodeLimits:
    """Hard limits a decoder must enforce *before* allocating pixel buffers."""

    max_input_bytes: int
    max_pixels: int  # width * height * frames; decompression-bomb guard
    max_frames: int = 1
    timeout_seconds: float = 30.0
    max_decoded_bytes: int = 2 << 30  # memory for the pixels, checked before they are decoded


@dataclass(frozen=True)
class DecodedImage:
    """Working representation. Original bytes are never modified or replaced."""

    source_sha256: str
    media_format: str
    width: int
    height: int
    channels: int
    bit_depth: int
    pixels: bytes  # row-major, interleaved channels
    icc_profile: bytes | None = None
    orientation: int = 1  # EXIF orientation as found; normalization is a separate step
    mode: str = ""  # working pixel mode, e.g. "RGB", "RGBA" or "I;16"
    conversion: str = "none"  # comma-separated codes for what changed; see the adapter
    transfer: str = "srgb"  # srgb | bt709 | pq | hlg | linear
    primaries: str = "bt709"  # bt709 | p3 | bt2020
    colour_notes: tuple[str, ...] = ()  # e.g. transfer_assumed, cicp_signalled


class ImageDecoder(Protocol):
    """A decoder for one or more formats.

    Implementations must: check ``limits`` from the header before decoding pixels;
    raise ``InputRejectedError`` for oversized, malformed or unsupported input; never
    write to disk; and report codec library versions for manifests.
    """

    formats: frozenset[str]

    def library_versions(self) -> dict[str, str]: ...

    def probe(self, data: bytes) -> str | None:
        """Return the format name if ``data`` is a format this decoder handles."""
        ...

    def decode(self, data: bytes, *, source_sha256: str, limits: DecodeLimits) -> DecodedImage: ...
