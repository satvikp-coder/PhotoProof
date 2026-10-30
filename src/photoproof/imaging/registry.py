"""Which decoder handles which format, and what is missing on this platform."""

from collections.abc import Iterable

from photoproof.contracts.exceptions import InputRejectedError
from photoproof.imaging.decoder import ImageDecoder
from photoproof.imaging.formats import FORMATS

# What to install when a format has no decoder.
_PACKAGES = {
    "jpeg": "Pillow",
    "png": "Pillow",
    "webp": "Pillow",
    "tiff": "Pillow",
    "avif": "Pillow with libavif",
    "heif": "pillow-heif",
    "jxl": "a libjxl binding",
}


class DecoderRegistry:
    def __init__(self) -> None:
        self._decoders: dict[str, ImageDecoder] = {}

    def register(self, decoder: ImageDecoder) -> None:
        """Add a decoder. Two decoders for one format is a programming error."""
        for name in decoder.formats:
            if name not in FORMATS:
                raise ValueError(f"unknown image format {name!r}; known: {FORMATS}")
            if name in self._decoders:
                raise ValueError(f"format {name!r} already has a decoder")
        self._decoders.update(dict.fromkeys(decoder.formats, decoder))

    def for_format(self, name: str) -> ImageDecoder:
        """The decoder for ``name``. Raises ``InputRejectedError`` if there is none."""
        decoder = self._decoders.get(name)
        if decoder is None:
            package = _PACKAGES.get(name, "a decoder for it")
            raise InputRejectedError(
                f"format {name} is unsupported on this platform: no decoder is available. "
                f"It needs {package}."
            )
        return decoder

    def available(self) -> frozenset[str]:
        return frozenset(self._decoders)

    def missing(self, accepted: Iterable[str]) -> dict[str, str]:
        """Accepted formats without a decoder, each with what it needs."""
        return {name: _PACKAGES[name] for name in accepted if name not in self._decoders}

    def library_versions(self) -> dict[str, str]:
        versions: dict[str, str] = {}
        for decoder in self._decoders.values():
            versions.update(decoder.library_versions())
        return versions
