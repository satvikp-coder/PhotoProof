"""Turn a decoded image into a documented working image, recording every change.

Colour conversion alters pixel statistics, so a working image is a separate view:
``normalize`` never changes the ``DecodedImage``, and compression and noise detectors can
keep using the unconverted pixels. Transform codes are stable, because manifests record them.

    assumed_srgb                no ICC profile; pixels are treated as sRGB
    icc_converted_to_srgb       converted from the embedded profile
    icc_invalid                 the profile could not be used; pixels left as they were
    icc_too_large               the profile is over 4 MiB, so it was ignored
    icc_not_applied_16_bit      16-bit grayscale keeps its depth, so it was not converted
    orientation_applied:N       EXIF orientation N (2-8) was applied
    orientation_invalid:N       EXIF orientation N is outside 1-8, so it was ignored
"""

import io
from dataclasses import dataclass, field
from typing import Literal

from PIL import Image, ImageCms

from photoproof.imaging.decoder import DecodedImage

MAX_ICC_BYTES = 4 << 20
_SIXTEEN_BIT_MODE = "I;16"

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


def normalize(decoded: DecodedImage, policy: NormalizationPolicy = DEFAULT_POLICY) -> WorkingImage:
    """Return the working image for ``decoded``, which is left unchanged.

    A bad ICC profile or orientation value is recorded rather than raised, because a
    malformed profile should not stop the analysis of otherwise valid pixels.
    """
    image = Image.frombytes(decoded.mode, (decoded.width, decoded.height), decoded.pixels)
    transforms: list[str] = []
    colour_space: Literal["srgb", "source"] = "source"
    if policy.convert_to_srgb:
        image, colour_space = _to_srgb(image, decoded.icc_profile, policy, transforms)
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
    )


def _to_srgb(
    image: Image.Image,
    icc_profile: bytes | None,
    policy: NormalizationPolicy,
    transforms: list[str],
) -> tuple[Image.Image, Literal["srgb", "source"]]:
    if icc_profile is None:
        transforms.append("assumed_srgb")
        return image, "srgb"
    if len(icc_profile) > MAX_ICC_BYTES:
        transforms.append("icc_too_large")
        return image, "source"
    if image.mode == _SIXTEEN_BIT_MODE:
        transforms.append("icc_not_applied_16_bit")
        return image, "source"
    try:
        source = ImageCms.ImageCmsProfile(io.BytesIO(icc_profile))
        srgb = ImageCms.ImageCmsProfile(ImageCms.createProfile("sRGB"))
        transform = ImageCms.buildTransform(
            source,
            srgb,
            image.mode,
            image.mode,
            renderingIntent=_INTENTS[policy.rendering_intent],
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
