"""Orientation by pixel position, ICC conversion against a computed reference, bad profiles."""

import dataclasses
import io
import struct

import pytest
from PIL import Image, ImageCms

from photoproof.imaging.adapters.pillow import PillowDecoder
from photoproof.imaging.decoder import DecodedImage, DecodeLimits
from photoproof.imaging.normalization import (
    MAX_ICC_BYTES,
    NormalizationPolicy,
    WorkingImage,
    normalize,
)

SRGB_PROFILE = ImageCms.ImageCmsProfile(ImageCms.createProfile("sRGB")).tobytes()
NO_COLOUR = NormalizationPolicy(convert_to_srgb=False)

# 3x2, every pixel distinct, so any wrong rotation or mirror is visible.
A, B, C = (10, 0, 0), (20, 0, 0), (30, 0, 0)
D, E, F = (40, 0, 0), (50, 0, 0), (60, 0, 0)
GRID = [[A, B, C], [D, E, F]]


def decoded(
    rows: list[list[tuple[int, ...]]] = GRID,
    *,
    mode: str = "RGB",
    orientation: int = 1,
    icc_profile: bytes | None = None,
) -> DecodedImage:
    pixels = b"".join(bytes(pixel) for row in rows for pixel in row)
    return DecodedImage(
        source_sha256="a" * 64,
        media_format="png",
        width=len(rows[0]),
        height=len(rows),
        channels=len(rows[0][0]),
        bit_depth=8,
        pixels=pixels,
        icc_profile=icc_profile,
        orientation=orientation,
        mode=mode,
    )


def rows_of(working: WorkingImage) -> list[list[tuple[int, ...]]]:
    size = len(Image.new(working.mode, (1, 1)).getbands())
    flat = [tuple(working.pixels[i : i + size]) for i in range(0, len(working.pixels), size)]
    return [flat[y * working.width : (y + 1) * working.width] for y in range(working.height)]


# ------------------------------------------------------------------------ orientation


@pytest.mark.parametrize(
    ("orientation", "expected"),
    [
        (1, [[A, B, C], [D, E, F]]),  # as stored
        (2, [[C, B, A], [F, E, D]]),  # mirrored horizontally
        (3, [[F, E, D], [C, B, A]]),  # rotated 180
        (4, [[D, E, F], [A, B, C]]),  # mirrored vertically
        (5, [[A, D], [B, E], [C, F]]),  # transposed
        (6, [[D, A], [E, B], [F, C]]),  # rotated 90 clockwise
        (7, [[F, C], [E, B], [D, A]]),  # transversed
        (8, [[C, F], [B, E], [A, D]]),  # rotated 90 counter-clockwise
    ],
)
def test_all_eight_orientations_put_pixels_in_the_right_place(
    orientation: int, expected: list[list[tuple[int, int, int]]]
) -> None:
    working = normalize(decoded(orientation=orientation), NO_COLOUR)
    assert rows_of(working) == expected
    assert (working.height, working.width) == (len(expected), len(expected[0]))
    assert working.orientation_applied is (orientation != 1)


def test_orientation_is_logged_when_applied() -> None:
    assert normalize(decoded(orientation=6), NO_COLOUR).transforms == ("orientation_applied:6",)


def test_orientation_one_logs_nothing() -> None:
    assert normalize(decoded(), NO_COLOUR).transforms == ()


@pytest.mark.parametrize("value", [0, 9, -1, 300])
def test_invalid_orientation_is_ignored_and_recorded(value: int) -> None:
    working = normalize(decoded(orientation=value), NO_COLOUR)
    assert rows_of(working) == GRID
    assert not working.orientation_applied
    assert working.transforms == (f"orientation_invalid:{value}",)


def test_orientation_can_be_turned_off() -> None:
    policy = NormalizationPolicy(apply_orientation=False, convert_to_srgb=False)
    working = normalize(decoded(orientation=6), policy)
    assert rows_of(working) == GRID
    assert not working.orientation_applied
    assert working.transforms == ()


# ------------------------------------------------------------------------ ICC profiles


def _s15_fixed16(value: float) -> bytes:
    return struct.pack(">i", round(value * 65536))


def _xyz_tag(x: float, y: float, z: float) -> bytes:
    return b"XYZ \x00\x00\x00\x00" + _s15_fixed16(x) + _s15_fixed16(y) + _s15_fixed16(z)


def _gamma_tag(gamma: float) -> bytes:
    return b"curv\x00\x00\x00\x00" + struct.pack(">IH", 1, round(gamma * 256)) + b"\x00\x00"


def _text_description(text: str) -> bytes:
    ascii_bytes = text.encode() + b"\x00"
    body = b"desc\x00\x00\x00\x00" + struct.pack(">I", len(ascii_bytes)) + ascii_bytes
    return body + b"\x00" * 8 + b"\x00" * 3 + b"\x00" + b"\x00" * 67


def matrix_profile(
    red: tuple[float, float, float],
    green: tuple[float, float, float],
    blue: tuple[float, float, float],
    gamma: float,
) -> bytes:
    """A real ICC v2 matrix-shaper RGB profile, built byte by byte."""
    tags = {
        b"desc": _text_description("synthetic"),
        b"wtpt": _xyz_tag(0.9642, 1.0, 0.8249),
        b"rXYZ": _xyz_tag(*red),
        b"gXYZ": _xyz_tag(*green),
        b"bXYZ": _xyz_tag(*blue),
        b"rTRC": _gamma_tag(gamma),
        b"gTRC": _gamma_tag(gamma),
        b"bTRC": _gamma_tag(gamma),
        b"cprt": b"text\x00\x00\x00\x00none\x00\x00\x00\x00",
    }
    table_size = 4 + 12 * len(tags)
    offset = 128 + table_size
    table, data = struct.pack(">I", len(tags)), b""
    for name, value in tags.items():
        table += name + struct.pack(">II", offset + len(data), len(value))
        data += value + b"\x00" * (-len(value) % 4)
    total = 128 + len(table) + len(data)
    header = bytearray(128)
    header[0:4] = struct.pack(">I", total)
    header[8:12] = bytes([2, 0x40, 0, 0])
    header[12:16], header[16:20], header[20:24] = b"mntr", b"RGB ", b"XYZ "
    header[36:40] = b"acsp"
    header[68:80] = _s15_fixed16(0.9642) + _s15_fixed16(1.0) + _s15_fixed16(0.8249)
    return bytes(header) + table + data


# Display P3 primaries, adapted to the D50 white point that ICC profiles use.
P3_RED, P3_GREEN, P3_BLUE = (
    (0.5151, 0.2412, -0.0011),
    (0.2920, 0.6922, 0.0419),
    (
        0.1571,
        0.0666,
        0.7841,
    ),
)
DISPLAY_P3_GAMMA_22 = matrix_profile(P3_RED, P3_GREEN, P3_BLUE, 2.2)
# The sRGB primaries, also adapted to D50.
XYZ_D50_TO_LINEAR_SRGB = (
    (3.1338561, -1.6168667, -0.4906146),
    (-0.9787684, 1.9161415, 0.0334540),
    (0.0719453, -0.2289914, 1.4052427),
)


def _reference_srgb(pixel: tuple[int, int, int]) -> tuple[int, int, int]:
    """Compute the expected result with plain arithmetic, independent of LittleCMS."""
    linear = [(value / 255) ** 2.2 for value in pixel]
    xyz = [
        sum(
            primary[axis] * amount
            for primary, amount in zip((P3_RED, P3_GREEN, P3_BLUE), linear, strict=True)
        )
        for axis in range(3)
    ]
    srgb_linear = [
        sum(c * v for c, v in zip(row, xyz, strict=True)) for row in XYZ_D50_TO_LINEAR_SRGB
    ]

    def encode(value: float) -> int:
        value = min(max(value, 0.0), 1.0)
        code = 12.92 * value if value <= 0.0031308 else 1.055 * value ** (1 / 2.4) - 0.055
        return round(code * 255)

    return (encode(srgb_linear[0]), encode(srgb_linear[1]), encode(srgb_linear[2]))


@pytest.mark.parametrize(
    "pixel",
    [(200, 100, 50), (128, 128, 128), (60, 160, 90), (90, 80, 200), (255, 255, 255), (0, 0, 0)],
)
def test_display_p3_converts_within_one_of_a_computed_reference(
    pixel: tuple[int, int, int],
) -> None:
    working = normalize(decoded([[pixel]], icc_profile=DISPLAY_P3_GAMMA_22))
    assert working.colour_space == "srgb"
    assert working.transforms == ("icc_converted_to_srgb",)
    for got, want in zip(working.pixels, _reference_srgb(pixel), strict=True):
        assert abs(got - want) <= 1, (tuple(working.pixels), _reference_srgb(pixel))


def test_a_wide_gamut_colour_really_changes() -> None:
    assert normalize(decoded([[(200, 100, 50)]], icc_profile=DISPLAY_P3_GAMMA_22)).pixels != bytes(
        (200, 100, 50)
    )


def test_srgb_profile_leaves_pixels_within_one() -> None:
    pixel = (200, 100, 50)
    working = normalize(decoded([[pixel]], icc_profile=SRGB_PROFILE))
    assert working.colour_space == "srgb"
    for got, want in zip(working.pixels, pixel, strict=True):
        assert abs(got - want) <= 1


def test_both_rendering_intents_work() -> None:
    for intent in ("perceptual", "relative_colorimetric"):
        policy = NormalizationPolicy(rendering_intent=intent)  # type: ignore[arg-type]
        working = normalize(decoded(icc_profile=DISPLAY_P3_GAMMA_22), policy)
        assert working.colour_space == "srgb"


def test_alpha_survives_conversion_untouched() -> None:
    rows = [[(200, 100, 50, 7), (60, 160, 90, 250)]]
    working = normalize(decoded(rows, mode="RGBA", icc_profile=DISPLAY_P3_GAMMA_22))
    assert working.mode == "RGBA"
    assert working.pixels[3] == 7 and working.pixels[7] == 250


def test_missing_profile_is_assumed_srgb_and_pixels_are_untouched() -> None:
    source = decoded()
    working = normalize(source)
    assert working.colour_space == "srgb"
    assert working.transforms == ("assumed_srgb",)
    assert working.pixels == source.pixels


@pytest.mark.parametrize(
    "profile",
    [
        b"",
        b"not a profile",
        b"\x00" * 128,
        b"acsp" * 100,
        SRGB_PROFILE[:64],
        SRGB_PROFILE[:-200],
        bytes(reversed(SRGB_PROFILE)),
    ],
)
def test_invalid_profile_is_recorded_and_does_not_stop_the_image(profile: bytes) -> None:
    source = decoded(icc_profile=profile)
    working = normalize(source)
    if working.transforms == ("icc_invalid",):
        assert working.colour_space == "source"
        assert working.pixels == source.pixels
    else:  # a damaged profile that LittleCMS still accepts is at least recorded as used
        assert working.transforms == ("icc_converted_to_srgb",)


def test_garbage_profile_is_definitely_icc_invalid() -> None:
    working = normalize(decoded(icc_profile=b"not a profile"))
    assert working.transforms == ("icc_invalid",)


def test_profile_for_another_colour_model_does_not_stop_the_image() -> None:
    lab = ImageCms.ImageCmsProfile(ImageCms.createProfile("LAB")).tobytes()
    assert normalize(decoded(icc_profile=lab)).transforms[0] in {
        "icc_invalid",
        "icc_converted_to_srgb",
    }


def test_oversized_profile_is_ignored_and_recorded() -> None:
    source = decoded(icc_profile=b"x" * (MAX_ICC_BYTES + 1))
    working = normalize(source)
    assert working.transforms == ("icc_too_large",)
    assert working.colour_space == "source"
    assert working.pixels == source.pixels


def test_conversion_can_be_turned_off() -> None:
    source = decoded(icc_profile=DISPLAY_P3_GAMMA_22)
    working = normalize(source, NO_COLOUR)
    assert working.colour_space == "source"
    assert working.pixels == source.pixels
    assert working.transforms == ()


# ------------------------------------------------------------------------- bit depth


def _sixteen_bit(icc_profile: bytes | None = None, orientation: int = 1) -> DecodedImage:
    return DecodedImage(
        source_sha256="a" * 64,
        media_format="png",
        width=2,
        height=1,
        channels=1,
        bit_depth=16,
        pixels=struct.pack("<HH", 1000, 65535),
        icc_profile=icc_profile,
        orientation=orientation,
        mode="I;16",
    )


def test_sixteen_bit_keeps_its_depth_and_records_no_conversion() -> None:
    source = _sixteen_bit(icc_profile=DISPLAY_P3_GAMMA_22)
    working = normalize(source)
    assert working.mode == "I;16"
    assert working.pixels == source.pixels
    assert working.colour_space == "source"
    assert working.transforms == ("icc_not_applied_16_bit",)


def test_sixteen_bit_orientation_keeps_values_exact() -> None:
    working = normalize(_sixteen_bit(orientation=3))
    assert working.pixels == struct.pack("<HH", 65535, 1000)


# -------------------------------------------------------------------------- isolation


def test_the_decoded_image_is_unchanged_and_still_usable() -> None:
    source = decoded(orientation=6, icc_profile=DISPLAY_P3_GAMMA_22)
    snapshot = dataclasses.replace(source)
    working = normalize(source)
    assert source == snapshot
    assert source.pixels != working.pixels
    assert normalize(source) == working  # deterministic, and the source is still valid


def test_transforms_are_logged_in_order() -> None:
    working = normalize(decoded(orientation=8, icc_profile=DISPLAY_P3_GAMMA_22))
    assert working.transforms == ("icc_converted_to_srgb", "orientation_applied:8")


def test_a_real_decoded_file_normalizes_end_to_end() -> None:
    image = Image.new("RGB", (3, 2), (120, 90, 60))
    exif = Image.Exif()
    exif[0x0112] = 6
    buffer = io.BytesIO()
    image.save(buffer, "JPEG", exif=exif, icc_profile=DISPLAY_P3_GAMMA_22, quality=95)
    limits = DecodeLimits(max_input_bytes=1 << 20, max_pixels=1000)
    source = PillowDecoder().decode(buffer.getvalue(), source_sha256="a" * 64, limits=limits)
    assert source.icc_profile == DISPLAY_P3_GAMMA_22
    working = normalize(source)
    assert (working.width, working.height) == (2, 3)  # rotated
    assert working.colour_space == "srgb"
    assert source.width == 3  # the decoded image still has the stored orientation
