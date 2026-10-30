"""Turn a decoded image into a documented working image, recording every change.

Colour conversion alters pixel statistics, so a working image is a separate view:
``normalize`` never changes the ``DecodedImage``, and compression and noise detectors can
keep using the unconverted pixels. Transform codes are stable, because manifests record them.

An image with more than 8 bits per channel gets two views: ``normalize`` gives the 8-bit image
that every detector can use, through the operator below, and ``normalize_hdr`` gives its
``uint16`` code values, untouched apart from orientation.

Tone mapping is one curve per channel, so it is deterministic and monotonic. SDR sources are
scaled proportionally. PQ and HLG are converted to display light, and an extended Reinhard
curve maps SDR white (203 nits) and peak (10000 nits for PQ, 1000 for HLG) into sRGB's range.
Alpha is always scaled proportionally.

    assumed_srgb                no ICC profile or CICP; pixels are treated as sRGB
    cicp_signalled              CICP describes the pixels, so any ICC profile was not used
    icc_converted_to_srgb       converted from the embedded profile
    icc_invalid                 the profile could not be used; pixels left as they were
    icc_too_large               the profile is over 4 MiB, so it was ignored
    tone_mapped:scale           high-depth SDR scaled to 8 bits, proportionally
    tone_mapped:srgb_oetf       linear light encoded with the sRGB curve
    tone_mapped:reinhard:...    HDR tone-mapped; parameters are in the code (see below)
    gamut_not_converted         the primaries are wider than sRGB and were kept
    hdr_8_bit_not_tone_mapped   an 8-bit PQ or HLG image, left as it was
    orientation_applied:N       EXIF orientation N (2-8) was applied
    orientation_invalid:N       EXIF orientation N is outside 1-8, so it was ignored
"""

import io
import math
from dataclasses import dataclass, field
from functools import lru_cache
from typing import Literal

from PIL import Image, ImageCms

from photoproof.contracts.exceptions import InputRejectedError
from photoproof.imaging.decoder import SIXTEEN_BIT_MODES, DecodedImage, DecodeLimits

MAX_ICC_BYTES = 4 << 20
_SDR_WHITE_NITS = 203.0
_PQ_PEAK_NITS = 10000.0
_HLG_PEAK_NITS = 1000.0
_HLG_SYSTEM_GAMMA = 1.2
_SIXTEEN_BIT_MAX = 65535
_EXPANDED_FROM_GRAY = frozenset({"gray_to_rgb", "gray_alpha_to_rgba"})

_ORIENTATIONS = {
    2: Image.Transpose.FLIP_LEFT_RIGHT,
    3: Image.Transpose.ROTATE_180,
    4: Image.Transpose.FLIP_TOP_BOTTOM,
    5: Image.Transpose.TRANSPOSE,
    6: Image.Transpose.ROTATE_270,
    7: Image.Transpose.TRANSVERSE,
    8: Image.Transpose.ROTATE_90,
}
_INTENTS = {
    "perceptual": ImageCms.Intent.PERCEPTUAL,
    "relative_colorimetric": ImageCms.Intent.RELATIVE_COLORIMETRIC,
}


@dataclass(frozen=True)
class NormalizationPolicy:
    apply_orientation: bool = True
    convert_to_srgb: bool = True
    rendering_intent: Literal["perceptual", "relative_colorimetric"] = "relative_colorimetric"


DEFAULT_POLICY = NormalizationPolicy()


@dataclass(frozen=True)
class WorkingImage:
    pixels: bytes
    mode: str
    width: int
    height: int
    colour_space: Literal["srgb", "source"]  # "source" means the pixels were not converted
    orientation_applied: bool
    transforms: tuple[str, ...] = field(default_factory=tuple)
    dtype: Literal["uint8", "uint16"] = "uint8"
    bit_depth: int = 8
    transfer: str = "srgb"
    primaries: str = "bt709"


def normalize(decoded: DecodedImage, policy: NormalizationPolicy = DEFAULT_POLICY) -> WorkingImage:
    """Return the working image for ``decoded``, which is left unchanged.

    A bad ICC profile or orientation value is recorded rather than raised, because a
    malformed profile should not stop the analysis of otherwise valid pixels.
    """
    transforms: list[str] = list(decoded.colour_notes)
    colour_space: Literal["srgb", "source"] = "source"
    conversion = decoded.conversion
    tone_mapped = decoded.mode in SIXTEEN_BIT_MODES
    if tone_mapped:
        image = _tone_map(decoded, transforms)
        if decoded.mode == "I;16":
            conversion = "gray_to_rgb"
    else:
        image = Image.frombytes(decoded.mode, (decoded.width, decoded.height), decoded.pixels)
        if decoded.transfer not in {"srgb", "bt709"}:
            transforms.append("hdr_8_bit_not_tone_mapped")  # too few bits to tone-map well
    if decoded.primaries != "bt709":
        transforms.append("gamut_not_converted")
    # BT.709 keeps its own curve, because scaling does not change it. Everything tone-mapped
    # from PQ, HLG or linear light is encoded with the sRGB curve.
    srgb_encoded = decoded.transfer == "srgb" or (tone_mapped and decoded.transfer != "bt709")
    if (
        policy.convert_to_srgb
        and decoded.transfer == "srgb"
        and decoded.primaries == "bt709"
        and "cicp_signalled" not in decoded.colour_notes
    ):
        image, colour_space = _to_srgb(image, decoded.icc_profile, conversion, policy, transforms)
    elif srgb_encoded and decoded.primaries == "bt709" and (policy.convert_to_srgb or tone_mapped):
        colour_space = "srgb"
    orientation_applied = False
    if policy.apply_orientation:
        image, orientation_applied = _apply_orientation(image, decoded.orientation, transforms)
    return WorkingImage(
        pixels=image.tobytes(),
        mode=image.mode,
        width=image.width,
        height=image.height,
        colour_space=colour_space,
        orientation_applied=orientation_applied,
        transforms=tuple(transforms),
        # Whatever the pixels now hold, whether converted, tone-mapped or left as signalled.
        transfer="srgb" if srgb_encoded or colour_space == "srgb" else decoded.transfer,
        primaries="bt709" if colour_space == "srgb" else decoded.primaries,
    )


def normalize_hdr(
    decoded: DecodedImage, limits: DecodeLimits, policy: NormalizationPolicy = DEFAULT_POLICY
) -> WorkingImage | None:
    """Return the ``uint16`` view of a high-depth image, or ``None`` for 8-bit sources.

    Code values are unchanged apart from orientation. Their meaning is in ``transfer``,
    ``primaries`` and ``bit_depth``, so plugins that need floats can convert them correctly.
    Raises ``InputRejectedError`` if the result would be over ``limits.max_decoded_bytes``.
    """
    channels = SIXTEEN_BIT_MODES.get(decoded.mode)
    if channels is None:
        return None
    needed = decoded.width * decoded.height * channels * 2
    if needed > limits.max_decoded_bytes:
        raise InputRejectedError(
            f"high-depth image would need {needed} bytes; the limit is {limits.max_decoded_bytes}"
        )
    transforms = list(decoded.colour_notes)
    planes = _split_sixteen_bit(decoded.pixels, channels, (decoded.width, decoded.height))
    orientation_applied = False
    if policy.apply_orientation:
        rotated = [_apply_orientation(plane, decoded.orientation, []) for plane in planes]
        planes = [plane for plane, _ in rotated]
        _, orientation_applied = _apply_orientation(planes[0], decoded.orientation, transforms)
    return WorkingImage(
        pixels=_merge_sixteen_bit(planes),
        mode=decoded.mode,
        width=planes[0].width,
        height=planes[0].height,
        colour_space="source",
        orientation_applied=orientation_applied,
        transforms=tuple(transforms),
        dtype="uint16",
        bit_depth=decoded.bit_depth,
        transfer=decoded.transfer,
        primaries=decoded.primaries,
    )


def _split_sixteen_bit(pixels: bytes, channels: int, size: tuple[int, int]) -> list[Image.Image]:
    stride = channels * 2
    planes = []
    for channel in range(channels):
        plane = bytearray(len(pixels) // channels)
        plane[0::2] = pixels[channel * 2 :: stride]
        plane[1::2] = pixels[channel * 2 + 1 :: stride]
        planes.append(Image.frombytes("I;16", size, bytes(plane)))
    return planes


def _merge_sixteen_bit(planes: list[Image.Image]) -> bytes:
    channels = len(planes)
    stride = channels * 2
    merged = bytearray(planes[0].width * planes[0].height * stride)
    for channel, image in enumerate(planes):
        plane = image.tobytes()
        merged[channel * 2 :: stride] = plane[0::2]
        merged[channel * 2 + 1 :: stride] = plane[1::2]
    return bytes(merged)


def _tone_map(decoded: DecodedImage, transforms: list[str]) -> Image.Image:
    """The 8-bit derivative of a high-depth image, as RGB or RGBA."""
    size = (decoded.width, decoded.height)
    channels = SIXTEEN_BIT_MODES[decoded.mode]
    planes = _split_sixteen_bit(decoded.pixels, channels, size)
    curve = tone_curve(decoded.transfer, decoded.bit_depth)
    transforms.append(_tone_code(decoded.transfer))
    mapped = [_apply_curve(plane, curve) for plane in planes[:3]]
    if channels == 1:
        return Image.merge("RGB", (mapped[0], mapped[0], mapped[0]))
    if channels == 3:
        return Image.merge("RGB", mapped)
    alpha = _apply_curve(planes[3], tone_curve("srgb", decoded.bit_depth))
    return Image.merge("RGBA", (*mapped, alpha))


def _apply_curve(plane: Image.Image, curve: list[int]) -> Image.Image:
    wide = Image.frombytes("I", plane.size, plane.tobytes(), "raw", "I;16")
    return wide.point(curve, "L")


def _tone_code(transfer: str) -> str:
    if transfer == "pq":
        return f"tone_mapped:reinhard:white={_SDR_WHITE_NITS:g}:peak={_PQ_PEAK_NITS:g}"
    if transfer == "hlg":
        return f"tone_mapped:reinhard:white={_SDR_WHITE_NITS:g}:peak={_HLG_PEAK_NITS:g}"
    if transfer == "linear":
        return "tone_mapped:srgb_oetf"
    return "tone_mapped:scale"


@lru_cache(maxsize=16)
def tone_curve(transfer: str, bit_depth: int) -> list[int]:
    """Map every 16-bit code value to 8 bits. Values above ``bit_depth``'s range saturate."""
    maximum = (1 << bit_depth) - 1
    curve = [0] * (_SIXTEEN_BIT_MAX + 1)
    for code in range(_SIXTEEN_BIT_MAX + 1):
        signal = min(code / maximum, 1.0)
        curve[code] = round(_tone_map_signal(transfer, signal) * 255)
    return curve


def _tone_map_signal(transfer: str, signal: float) -> float:
    if transfer == "pq":
        return _srgb_oetf(_reinhard(_pq_to_nits(signal), _PQ_PEAK_NITS))
    if transfer == "hlg":
        return _srgb_oetf(_reinhard(_hlg_to_nits(signal), _HLG_PEAK_NITS))
    if transfer == "linear":
        return _srgb_oetf(signal)
    return signal  # already display-referred SDR, so only the scale changes


def _pq_to_nits(signal: float) -> float:
    """The SMPTE ST 2084 EOTF."""
    m1, m2 = 2610 / 16384, 2523 / 4096 * 128
    c1, c2, c3 = 3424 / 4096, 2413 / 4096 * 32, 2392 / 4096 * 32
    power = signal ** (1 / m2)
    return float(_PQ_PEAK_NITS * (max(power - c1, 0.0) / (c2 - c3 * power)) ** (1 / m1))


def _hlg_to_nits(signal: float) -> float:
    """The ARIB STD-B67 inverse OETF, then its system gamma at a 1000-nit display."""
    a, b, c = 0.17883277, 0.28466892, 0.55991073
    scene = signal * signal / 3 if signal <= 0.5 else (math.exp((signal - c) / a) + b) / 12
    return float(_HLG_PEAK_NITS * scene**_HLG_SYSTEM_GAMMA)


def _reinhard(nits: float, peak: float) -> float:
    """Extended Reinhard, relative to SDR white. Reaches exactly 1.0 at ``peak``."""
    light = nits / _SDR_WHITE_NITS
    white = peak / _SDR_WHITE_NITS
    return min(light * (1 + light / (white * white)) / (1 + light), 1.0)


def _srgb_oetf(linear: float) -> float:
    return 12.92 * linear if linear <= 0.0031308 else 1.055 * linear ** (1 / 2.4) - 0.055


def _gray_profile_to_srgb(
    image: Image.Image,
    source: ImageCms.ImageCmsProfile,
    srgb: ImageCms.ImageCmsProfile,
    intent: ImageCms.Intent,
) -> Image.Image | None:
    """Decoding expanded grayscale to RGB, but its profile still describes one channel."""
    transform = ImageCms.buildTransform(source, srgb, "L", "RGB", renderingIntent=intent)
    converted = ImageCms.applyTransform(image.getchannel("R"), transform)
    if converted is not None and image.mode == "RGBA":
        converted.putalpha(image.getchannel("A"))
    return converted


def _to_srgb(
    image: Image.Image,
    icc_profile: bytes | None,
    conversion: str,
    policy: NormalizationPolicy,
    transforms: list[str],
) -> tuple[Image.Image, Literal["srgb", "source"]]:
    if icc_profile is None:
        transforms.append("assumed_srgb")
        return image, "srgb"
    if len(icc_profile) > MAX_ICC_BYTES:
        transforms.append("icc_too_large")
        return image, "source"
    try:
        source = ImageCms.ImageCmsProfile(io.BytesIO(icc_profile))
        srgb = ImageCms.ImageCmsProfile(ImageCms.createProfile("sRGB"))
        intent = _INTENTS[policy.rendering_intent]
        expanded_from_gray = bool(_EXPANDED_FROM_GRAY.intersection(conversion.split(",")))
        if expanded_from_gray and source.profile.xcolor_space.strip() == "GRAY":
            converted = _gray_profile_to_srgb(image, source, srgb, intent)
        else:
            transform = ImageCms.buildTransform(
                source, srgb, image.mode, image.mode, renderingIntent=intent
            )
            converted = ImageCms.applyTransform(image, transform)
    # Hostile profiles have caused LittleCMS bugs, and they fail in many different ways.
    except Exception:  # noqa: BLE001 - recorded in the transform log, which the manifest keeps
        transforms.append("icc_invalid")
        return image, "source"
    if converted is None:  # only with inPlace=True, which is not used; satisfies the types
        transforms.append("icc_invalid")
        return image, "source"
    transforms.append("icc_converted_to_srgb")
    return converted, "srgb"


def _apply_orientation(
    image: Image.Image, orientation: int, transforms: list[str]
) -> tuple[Image.Image, bool]:
    if orientation == 1:
        return image, False
    transpose = _ORIENTATIONS.get(orientation)
    if transpose is None:
        transforms.append(f"orientation_invalid:{orientation}")
        return image, False
    transforms.append(f"orientation_applied:{orientation}")
    return image.transpose(transpose), True
