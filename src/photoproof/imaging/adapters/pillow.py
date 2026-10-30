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
    16-bit colour PNG           RGB or RGBA     16_bit_to_8_bit (Pillow keeps 8 bits; see #5)
    16-bit gray with alpha      RGBA            16_bit_to_8_bit,gray_alpha_to_rgba
    16-bit colour with tRNS     rejected        Pillow would compute the wrong alpha
    CMYK or YCCK JPEG           RGB             cmyk_to_rgb (naive, ignores any ICC profile)
"""

import io
import threading
import warnings
import zlib

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
_PNG_GRAYSCALE = 0
_PNG_GRAY_ALPHA = 4
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


def _check_png_chunks(data: bytes) -> None:
    """Walk every chunk to IEND, verifying checksums. Pillow skips the IDAT checksum, so an
    edit to pixel data that still inflates would otherwise pass. Data after IEND is allowed."""
    offset = len(_PNG_SIGNATURE)
    while offset + _PNG_CHUNK_OVERHEAD <= len(data):
        length = int.from_bytes(data[offset : offset + 4], "big")
        kind = data[offset + 4 : offset + 8]
        end = offset + 8 + length
        if end + 4 > len(data):
            break
        if zlib.crc32(data[offset + 4 : end]) != int.from_bytes(data[end : end + 4], "big"):
            raise InputRejectedError(f"malformed png: bad checksum in {kind!r} chunk")
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


def _too_many_pixels(media_format: str, limits: DecodeLimits) -> str:
    return f"{media_format} is over the limit of {limits.max_pixels} pixels"


def _to_decoded(
    image: Image.Image, data: bytes, media_format: str, source_sha256: str
) -> DecodedImage:
    working, conversion, bit_depth = _to_working_mode(image, data, media_format)
    icc_profile = image.info.get("icc_profile") or None
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
            if depth_note != "none":  # Pillow compares the wrong bytes of the 16-bit key here
                raise InputRejectedError(
                    f"{media_format}: 16-bit colour with transparency is not supported yet (#5)"
                )
            return image.convert("RGBA"), "transparency_to_rgba", 8
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
        if data[_PNG_COLOUR_TYPE_OFFSET] == _PNG_GRAY_ALPHA:  # one channel, so its profile is too
            return "16_bit_to_8_bit,gray_alpha_to_rgba"
        return "16_bit_to_8_bit"
    return "none"


def _orientation(image: Image.Image) -> int:
    """The EXIF orientation as found, not applied. Anything invalid counts as absent (1)."""
    try:
        value = image.getexif().get(_ORIENTATION_TAG, 1)
    except Exception:  # noqa: BLE001 - malformed EXIF must not reject a valid image
        return 1
    return value if isinstance(value, int) and 1 <= value <= 8 else 1
