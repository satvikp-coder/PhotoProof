"""Identify image formats from their signatures. File extensions are not trusted.

``detect_format`` only labels a file. A file can match a signature and still be invalid or
a polyglot, so decoders must validate the full structure themselves.
"""

import struct
from pathlib import PurePath

HEADER_BYTES = 64  # detect_format never looks further than this
_MINIMUM_BYTES = 12  # shorter than any complete signature check needs

FORMATS = ("jpeg", "png", "webp", "heif", "avif", "jxl", "tiff")

_AVIF_BRANDS = frozenset({b"avif", b"avis"})
_HEIF_MAJOR_BRANDS = frozenset({b"heic", b"heix"})
_HEIF_BRANDS = _HEIF_MAJOR_BRANDS | {b"mif1"}
_JXL_CONTAINER = bytes.fromhex("0000000C4A584C200D0A870A")

_EXTENSIONS = {
    ".jpg": "jpeg",
    ".jpeg": "jpeg",
    ".png": "png",
    ".webp": "webp",
    ".heif": "heif",
    ".heic": "heif",
    ".avif": "avif",
    ".jxl": "jxl",
    ".tif": "tiff",
    ".tiff": "tiff",
}


def detect_format(header: bytes) -> str | None:
    """Return the format name from the first bytes of a file, or ``None``.

    Never raises for any input. A polyglot gets the first match in this fixed order: JPEG,
    PNG, WebP, JPEG XL, TIFF, ISO-BMFF (AVIF, then HEIF).
    """
    head = bytes(header[:HEADER_BYTES])
    if len(head) < _MINIMUM_BYTES:
        return None
    if head.startswith(b"\xff\xd8\xff"):
        return "jpeg"
    if head.startswith(b"\x89PNG\r\n\x1a\n"):
        return "png"
    if head[:4] == b"RIFF" and head[8:12] == b"WEBP":
        return "webp"
    if head.startswith(b"\xff\x0a") or head.startswith(_JXL_CONTAINER):
        return "jxl"
    if head.startswith((b"II*\x00", b"MM\x00*")):
        return "tiff"
    return _detect_iso_bmff(head)


def _detect_iso_bmff(head: bytes) -> str | None:
    """Read the ``ftyp`` box. Its major brand decides, then its compatible brands.

    AVIF and HEIC files both commonly list ``mif1``, so it only means HEIF when nothing more
    specific appears.
    """
    if head[4:8] != b"ftyp" or len(head) < 16:
        return None
    box_size = struct.unpack(">I", head[:4])[0]
    if box_size == 0:  # the box runs to the end of the file
        box_size = len(head)
    if box_size < 16:  # includes 1, which is a 64-bit size no real ftyp box needs
        return None
    major = head[8:12]
    if major in _AVIF_BRANDS:
        return "avif"
    if major in _HEIF_MAJOR_BRANDS:
        return "heif"
    end = min(box_size, len(head))
    compatible = {head[offset : offset + 4] for offset in range(16, end - 3, 4)}
    if compatible & _AVIF_BRANDS:
        return "avif"
    if major in _HEIF_BRANDS or compatible & _HEIF_BRANDS:
        return "heif"
    return None


def format_from_name(name: str) -> str | None:
    """The format a file name claims. Used only to note mismatches, never to decide."""
    return _EXTENSIONS.get(PurePath(name).suffix.lower())
