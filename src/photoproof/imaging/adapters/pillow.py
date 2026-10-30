"""Decode JPEG, PNG and WebP through Pillow.

Every input is treated as hostile. Dimensions are checked from the header before any pixel
is allocated, and only the detected format's parser is allowed to run. Decoding runs in the
calling process until #9, so it has no wall-clock limit yet.

Working modes. ``DecodedImage.conversion`` records how each was reached, as comma-separated codes:

    Source                      Working mode    Conversion
    RGB, RGBA                   same            none
    grayscale, 1-bit            RGB             gray_to_rgb
    palette                     RGB or RGBA     palette_to_rgb, palette_to_rgba
    grayscale with alpha        RGBA            gray_alpha_to_rgba
    transparency chunk          RGBA            transparency_to_rgba
    16-bit grayscale PNG        I;16            none (little-endian); rejected if transparent
    16-bit colour PNG           RGB;16, RGBA;16 none (little-endian, lossless)
    16-bit gray with alpha      RGBA            16_bit_to_8_bit
    16-bit colour, interlaced   RGB or RGBA     16_bit_to_8_bit
    16-bit colour with tRNS     RGBA            16_bit_to_8_bit,transparency_to_rgba
    CMYK or YCCK JPEG           RGB             cmyk_to_rgb (naive, ignores any ICC profile)
"""

import io
import threading
import warnings
import zlib
from collections.abc import Iterator

import PIL
from PIL import Image, ImageFile, features

from photoproof.contracts.exceptions import InputRejectedError
from photoproof.imaging.decoder import DecodedImage, DecodeLimits
from photoproof.imaging.formats import detect_format

ImageFile.LOAD_TRUNCATED_IMAGES = False  # truncated input must be rejected, never half decoded

_PILLOW_FORMATS = {"jpeg": "JPEG", "png": "PNG", "webp": "WEBP"}
_PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"
_PNG_CHUNK_OVERHEAD = 12  # length, type and checksum
_VP8X_END = 30
_VP8L_END = 25
_VP8_END = 30
_VP8L_SIGNATURE = 0x2F
_VP8_START_CODE = b"\x9d\x01\x2a"
_WEBP_14_BITS = 0x3FFF
_PNG_BIT_DEPTH_OFFSET = 24
_PNG_COLOUR_TYPE_OFFSET = 25
_PNG_INTERLACE_OFFSET = 28
_PNG_COLOUR_WITH_LOSSLESS_PATH = frozenset({2, 6})  # RGB and RGBA
_MAX_CHANNELS = 4
_CICP_BYTES = 4
_CICP_PRIMARIES = {1: "bt709", 9: "bt2020", 12: "p3"}  # ITU-T H.273
_CICP_TRANSFERS = {1: "bt709", 6: "bt709", 8: "linear", 13: "srgb", 14: "bt709", 15: "bt709"}
_CICP_TRANSFERS |= {16: "pq", 18: "hlg"}
_PNG_GRAYSCALE = 0
_ORIENTATION_TAG = 0x0112
_SIXTEEN_BIT_GRAY = frozenset({"I;16", "I;16L", "I;16B", "I"})
_open_lock = threading.Lock()  # Image.MAX_IMAGE_PIXELS is process-wide


class PillowDecoder:
    formats = frozenset(_PILLOW_FORMATS)

    def library_versions(self) -> dict[str, str]:
        versions = {"Pillow": PIL.__version__}
        for feature in ("jpg", "webp", "zlib"):
            version = features.version(feature)
            if version:
                versions[f"Pillow:{feature}"] = version
        return versions

    def probe(self, data: bytes) -> str | None:
        found = detect_format(data)
        return found if found in self.formats else None

    def decode(self, data: bytes, *, source_sha256: str, limits: DecodeLimits) -> DecodedImage:
        """Return the validated image. Raises ``InputRejectedError`` for anything else.

        Original bytes are never modified, and nothing is written to disk. With
        ``limits.max_frames > 1``, an animation is accepted and its first frame returned.
        """
        if len(data) > limits.max_input_bytes:
            raise InputRejectedError(f"input exceeds {limits.max_input_bytes} bytes")
        media_format = self.probe(data)
        if media_format is None:
            raise InputRejectedError("not a JPEG, PNG or WebP file")
        _check_complete(data, media_format)
        if media_format == "webp":
            _check_webp_canvas(data, limits)
        try:
            with _open_header(data, media_format, limits) as image:
                _check_header(image, media_format, limits)
                _check_memory(image, data, media_format, limits)
                if _is_lossless_sixteen_bit_colour(image, data, media_format):
                    return _decode_sixteen_bit_colour(image, data, source_sha256)
                image.load()
                return _to_decoded(image, data, media_format, source_sha256)
        except InputRejectedError:
            raise
        except Image.DecompressionBombError as error:
            raise InputRejectedError(_too_many_pixels(media_format, limits)) from error
        # Parsers of hostile input raise many types. All of them mean the same thing here.
        except Exception as error:
            raise InputRejectedError(
                f"malformed {media_format}: {type(error).__name__}: {error}"
            ) from error


def _check_complete(data: bytes, media_format: str) -> None:
    """Reject truncation that the parsers forgive once all the pixels have been read."""
    if media_format == "png":
        _check_png_chunks(data)
    # ponytail: an EOI inside an EXIF thumbnail also passes; the parser rejects any
    # truncation that loses pixels, so this only tightens the case of a lost end marker.
    elif media_format == "jpeg" and data.rfind(b"\xff\xd9") < 0:
        raise InputRejectedError("malformed jpeg: no end-of-image marker; the file is truncated")


def _check_webp_canvas(data: bytes, limits: DecodeLimits) -> None:
    """Check WebP dimensions by hand: ``Image.open`` is not header-only for WebP. It builds a
    native decoder that allocates the canvas before Pillow's own size check can run."""
    chunk = data[12:16]
    width = height = 0
    if chunk == b"VP8X" and len(data) >= _VP8X_END:
        width = int.from_bytes(data[24:27], "little") + 1
        height = int.from_bytes(data[27:30], "little") + 1
    elif chunk == b"VP8L" and len(data) >= _VP8L_END and data[20] == _VP8L_SIGNATURE:
        bits = int.from_bytes(data[21:25], "little")
        width = (bits & _WEBP_14_BITS) + 1
        height = ((bits >> 14) & _WEBP_14_BITS) + 1
    elif chunk == b"VP8 " and len(data) >= _VP8_END and data[23:26] == _VP8_START_CODE:
        width = int.from_bytes(data[26:28], "little") & _WEBP_14_BITS
        height = int.from_bytes(data[28:30], "little") & _WEBP_14_BITS
    if width <= 0 or height <= 0:
        raise InputRejectedError("malformed webp: no valid image header")
    if width * height > limits.max_pixels:
        raise InputRejectedError(_too_many_pixels("webp", limits))
    _check_decoded_bytes(width, height, "webp", 1, limits)


def _check_png_chunks(data: bytes) -> None:
    """Walk every chunk to IEND, verifying checksums. Pillow skips the IDAT checksum, so an
    edit to pixel data that still inflates would otherwise pass. Data after IEND is allowed."""
    for _ in _png_chunks(data):
        pass


def _png_chunks(data: bytes) -> Iterator[tuple[bytes, bytes]]:
    """Yield each chunk's type and body, verified. Raises ``InputRejectedError`` if damaged."""
    offset = len(_PNG_SIGNATURE)
    while offset + _PNG_CHUNK_OVERHEAD <= len(data):
        length = int.from_bytes(data[offset : offset + 4], "big")
        kind = data[offset + 4 : offset + 8]
        end = offset + 8 + length
        if end + 4 > len(data):
            break
        if zlib.crc32(data[offset + 4 : end]) != int.from_bytes(data[end : end + 4], "big"):
            raise InputRejectedError(f"malformed png: bad checksum in {kind!r} chunk")
        yield kind, data[offset + 8 : end]
        if kind == b"IEND":
            return
        offset = end + 4
    raise InputRejectedError("malformed png: no complete IEND chunk; the file is truncated")


def _open_header(data: bytes, media_format: str, limits: DecodeLimits) -> Image.Image:
    """Parse only the header, allowing only one parser, with this call's pixel limit."""
    with _open_lock, warnings.catch_warnings():
        warnings.simplefilter("error", Image.DecompressionBombWarning)
        saved = Image.MAX_IMAGE_PIXELS
        Image.MAX_IMAGE_PIXELS = limits.max_pixels
        try:
            return Image.open(io.BytesIO(data), formats=[_PILLOW_FORMATS[media_format]])
        except Image.DecompressionBombWarning as error:
            raise InputRejectedError(_too_many_pixels(media_format, limits)) from error
        finally:
            Image.MAX_IMAGE_PIXELS = saved


def _check_header(image: Image.Image, media_format: str, limits: DecodeLimits) -> None:
    width, height = image.size
    if width <= 0 or height <= 0:
        raise InputRejectedError(f"malformed {media_format}: dimensions are {width}x{height}")
    frames = getattr(image, "n_frames", 1)
    if frames > limits.max_frames:
        raise InputRejectedError(
            f"{media_format} has {frames} frames; the limit is {limits.max_frames}"
        )
    if width * height * frames > limits.max_pixels:
        raise InputRejectedError(_too_many_pixels(media_format, limits))


def _check_memory(image: Image.Image, data: bytes, media_format: str, limits: DecodeLimits) -> None:
    """Estimate the working buffer before allocating it: four channels at the source's depth."""
    sample_bytes = 2 if media_format == "png" and data[_PNG_BIT_DEPTH_OFFSET] == 16 else 1
    _check_decoded_bytes(image.width, image.height, media_format, sample_bytes, limits)


def _check_decoded_bytes(
    width: int, height: int, media_format: str, sample_bytes: int, limits: DecodeLimits
) -> None:
    needed = width * height * _MAX_CHANNELS * sample_bytes
    if needed > limits.max_decoded_bytes:
        raise InputRejectedError(
            f"{media_format} would need {needed} bytes of memory; "
            f"the limit is {limits.max_decoded_bytes}"
        )


def _is_lossless_sixteen_bit_colour(image: Image.Image, data: bytes, media_format: str) -> bool:
    return (
        media_format == "png"
        and data[_PNG_BIT_DEPTH_OFFSET] == 16
        and data[_PNG_COLOUR_TYPE_OFFSET] in _PNG_COLOUR_WITH_LOSSLESS_PATH
        and data[_PNG_INTERLACE_OFFSET] == 0
        and "transparency" not in image.info
    )


def _decode_sixteen_bit_colour(image: Image.Image, data: bytes, source_sha256: str) -> DecodedImage:
    """Keep every bit of a 16-bit colour PNG, which ``Image.load`` would reduce to 8.

    Pillow's fast C decoder can return either byte of each sample, depending on the raw mode,
    and it applies PNG's row filters either way. So the file is decoded twice, once for each
    byte, and the two are interleaved. A pure-Python decoder would take minutes on a photo.
    """
    mode = image.mode
    compressed = b"".join(body for kind, body in _png_chunks(data) if kind == b"IDAT")
    high = Image.frombytes(mode, image.size, compressed, "zip", f"{mode};16B").tobytes()
    low = Image.frombytes(mode, image.size, compressed, "zip", f"{mode};16L").tobytes()
    pixels = bytearray(len(high) * 2)
    pixels[0::2] = low
    pixels[1::2] = high
    transfer, primaries, notes = _colour_metadata(image, data, "png", bit_depth=16)
    return DecodedImage(
        source_sha256=source_sha256,
        media_format="png",
        width=image.width,
        height=image.height,
        channels=len(image.getbands()),
        bit_depth=16,
        pixels=bytes(pixels),
        icc_profile=image.info.get("icc_profile") or None,
        orientation=_orientation(image),
        mode=f"{mode};16",
        transfer=transfer,
        primaries=primaries,
        colour_notes=notes,
    )


def _colour_metadata(
    image: Image.Image, data: bytes, media_format: str, *, bit_depth: int
) -> tuple[str, str, tuple[str, ...]]:
    """Read transfer and primaries from a PNG ``cICP`` chunk, the only source today.

    Fully understood CICP is authoritative (``cicp_signalled``) and wins over an ICC profile,
    which is recorded too. Otherwise a high-depth image is treated as SDR, and that is recorded.
    """
    transfer, primaries = "srgb", "bt709"
    notes: list[str] = []
    cicp = None
    if media_format == "png":
        cicp = next((body for kind, body in _png_chunks(data) if kind == b"cICP"), None)
    if cicp is not None and len(cicp) == _CICP_BYTES:
        known_primaries = _CICP_PRIMARIES.get(cicp[0])
        known_transfer = _CICP_TRANSFERS.get(cicp[1])
        primaries = known_primaries or primaries
        transfer = known_transfer or transfer
        if known_primaries is None:
            notes.append("primaries_assumed")
        if known_transfer is None:
            notes.append("transfer_assumed")
        if known_primaries and known_transfer:
            notes.append("cicp_signalled")
            if image.info.get("icc_profile"):
                notes.append("cicp_preferred_over_icc")
    elif bit_depth > 8:
        notes.append("transfer_assumed")
    return transfer, primaries, tuple(notes)


def _too_many_pixels(media_format: str, limits: DecodeLimits) -> str:
    return f"{media_format} is over the limit of {limits.max_pixels} pixels"


def _to_decoded(
    image: Image.Image, data: bytes, media_format: str, source_sha256: str
) -> DecodedImage:
    working, conversion, bit_depth = _to_working_mode(image, data, media_format)
    icc_profile = image.info.get("icc_profile") or None
    transfer, primaries, notes = _colour_metadata(image, data, media_format, bit_depth=bit_depth)
    return DecodedImage(
        source_sha256=source_sha256,
        media_format=media_format,
        width=working.width,
        height=working.height,
        channels=len(working.getbands()),
        bit_depth=bit_depth,
        pixels=working.tobytes(),
        icc_profile=icc_profile,
        orientation=_orientation(image),
        mode=working.mode,
        conversion=conversion,
        transfer=transfer,
        primaries=primaries,
        colour_notes=notes,
    )


def _to_working_mode(
    image: Image.Image, data: bytes, media_format: str
) -> tuple[Image.Image, str, int]:
    mode = image.mode
    has_transparency = "transparency" in image.info
    if mode in _SIXTEEN_BIT_GRAY:
        if has_transparency:  # no working mode can carry it, and dropping it would be silent
            raise InputRejectedError(
                f"{media_format}: 16-bit grayscale with transparency is not supported yet (#5)"
            )
        return _to_sixteen_bit_little_endian(image), "none", 16
    if mode in {"RGB", "RGBA"}:
        depth_note = _colour_depth_note(data, media_format)
        if mode == "RGB" and has_transparency:
            notes = "transparency_to_rgba"
            if depth_note != "none":
                notes = f"{depth_note},{notes}"
            return image.convert("RGBA"), notes, 8
        return image, depth_note, 8
    if mode in {"L", "1"}:
        target = "RGBA" if has_transparency else "RGB"
        note = "transparency_to_rgba" if has_transparency else "gray_to_rgb"
        return image.convert(target), note, 8
    if mode == "P":
        target = "RGBA" if has_transparency else "RGB"
        return image.convert(target), f"palette_to_{target.lower()}", 8
    if mode in {"LA", "La", "PA"}:
        return image.convert("RGBA"), "gray_alpha_to_rgba", 8
    if mode == "CMYK":
        return image.convert("RGB"), "cmyk_to_rgb", 8
    raise InputRejectedError(f"{media_format} has unsupported pixel mode {mode}")


def _to_sixteen_bit_little_endian(image: Image.Image) -> Image.Image:
    if image.mode == "I;16B":
        return Image.frombytes("I;16", image.size, image.tobytes("raw", "I;16B"))
    if image.mode == "I":  # older Pillow reads 16-bit PNGs into 32-bit integers
        return Image.frombytes("I;16", image.size, image.tobytes("raw", "I;16"))
    return image


def _colour_depth_note(data: bytes, media_format: str) -> str:
    """Pillow reads 16-bit colour PNGs as 8-bit without saying so. The header does."""
    if (
        media_format == "png"
        and data[_PNG_BIT_DEPTH_OFFSET] == 16
        and data[_PNG_COLOUR_TYPE_OFFSET] != _PNG_GRAYSCALE
    ):
        return "16_bit_to_8_bit"
    return "none"


def _orientation(image: Image.Image) -> int:
    """The EXIF orientation as found, not applied. Anything invalid counts as absent (1)."""
    try:
        value = image.getexif().get(_ORIENTATION_TAG, 1)
    except Exception:  # noqa: BLE001 - malformed EXIF must not reject a valid image
        return 1
    return value if isinstance(value, int) and 1 <= value <= 8 else 1
