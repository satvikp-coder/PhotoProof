"""Lossless 16-bit decoding, colour metadata, the uint16 view and the 8-bit derivative."""

import dataclasses
import itertools
import random
import struct
import zlib

import pytest
from PIL import Image

from photoproof.contracts.exceptions import InputRejectedError
from photoproof.imaging.adapters.pillow import PillowDecoder
from photoproof.imaging.decoder import DecodedImage, DecodeLimits
from photoproof.imaging.normalization import (
    NormalizationPolicy,
    normalize,
    normalize_hdr,
    tone_curve,
)

LIMITS = DecodeLimits(max_input_bytes=10 << 20, max_pixels=10_000_000)
FILTERS = (0, 1, 2, 3, 4)


def _matrix_profile() -> bytes:
    """Display P3 as a real ICC profile, reused from the normalization tests."""
    from test_normalization import DISPLAY_P3_GAMMA_22

    return DISPLAY_P3_GAMMA_22


DISPLAY_P3 = _matrix_profile()


def chunk(kind: bytes, body: bytes) -> bytes:
    return struct.pack(">I", len(body)) + kind + body + struct.pack(">I", zlib.crc32(kind + body))


def _paeth(a: int, b: int, c: int) -> int:
    pa, pb, pc = abs(b - c), abs(a - c), abs(a + b - 2 * c)
    return a if pa <= pb and pa <= pc else b if pb <= pc else c


def filtered_rows(data: bytes, width: int, height: int, sample_bytes: int) -> bytes:
    """Encode rows using every PNG filter in turn, so the decoder has to undo them all."""
    stride = width * sample_bytes
    previous = bytes(stride)
    output = bytearray()
    for y in range(height):
        row = data[y * stride : (y + 1) * stride]
        kind = FILTERS[y % len(FILTERS)]
        encoded = bytearray(stride)
        for i in range(stride):
            a = row[i - sample_bytes] if i >= sample_bytes else 0
            b = previous[i]
            c = previous[i - sample_bytes] if i >= sample_bytes else 0
            prediction = (0, a, b, (a + b) // 2, _paeth(a, b, c))[kind]
            encoded[i] = (row[i] - prediction) & 0xFF
        output += bytes([kind]) + encoded
        previous = row
    return bytes(output)


def png_16(
    samples: list[int],
    width: int,
    height: int,
    colour: int,
    *,
    extra: bytes = b"",
    interlace: int = 0,
) -> bytes:
    channels = {0: 1, 2: 3, 4: 2, 6: 4}[colour]
    data = b"".join(struct.pack(">H", sample) for sample in samples)
    ihdr = struct.pack(">IIBBBBB", width, height, 16, colour, 0, 0, interlace)
    idat = zlib.compress(filtered_rows(data, width, height, channels * 2))
    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", ihdr)
        + extra
        + chunk(b"IDAT", idat)
        + chunk(b"IEND", b"")
    )


def decode(data: bytes, limits: DecodeLimits = LIMITS) -> DecodedImage:
    return PillowDecoder().decode(data, source_sha256="a" * 64, limits=limits)


def little_endian(samples: list[int]) -> bytes:
    return b"".join(struct.pack("<H", sample) for sample in samples)


def random_samples(count: int, seed: int) -> list[int]:
    generator = random.Random(seed)  # noqa: S311 - reproducible test data
    return [generator.randrange(65536) for _ in range(count)]


# ------------------------------------------------------------------- lossless decoding


@pytest.mark.parametrize(
    ("colour", "channels", "mode"), [(0, 1, "I;16"), (2, 3, "RGB;16"), (6, 4, "RGBA;16")]
)
def test_sixteen_bit_png_round_trips_without_loss_through_every_filter(
    colour: int, channels: int, mode: str
) -> None:
    width, height = 13, 11
    samples = random_samples(width * height * channels, seed=colour)
    decoded = decode(png_16(samples, width, height, colour))
    assert (decoded.mode, decoded.bit_depth, decoded.channels) == (mode, 16, channels)
    assert decoded.conversion == "none"
    assert decoded.pixels == little_endian(samples)


def test_low_bytes_really_differ_from_high_bytes() -> None:
    decoded = decode(png_16([0x0102, 0x0304, 0x0506], 1, 1, 2))
    assert decoded.pixels == bytes([0x02, 0x01, 0x04, 0x03, 0x06, 0x05])


def test_interlaced_sixteen_bit_colour_records_the_loss_instead_of_hiding_it() -> None:
    row = b"\x00" + struct.pack(">HHH", 0x1234, 0x5678, 0x9ABC)
    ihdr = struct.pack(">IIBBBBB", 1, 1, 16, 2, 0, 0, 1)
    data = (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", ihdr)
        + chunk(b"IDAT", zlib.compress(row))
        + chunk(b"IEND", b"")
    )
    decoded = decode(data)
    assert (decoded.bit_depth, decoded.conversion) == (8, "16_bit_to_8_bit")


def test_truncated_pixel_data_is_rejected() -> None:
    ihdr = struct.pack(">IIBBBBB", 4, 4, 16, 2, 0, 0, 0)
    short = zlib.compress(b"\x00" + b"\x12" * 6)  # one pixel of the sixteen it claims
    data = b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", ihdr) + chunk(b"IDAT", short) + chunk(b"IEND", b"")
    with pytest.raises(InputRejectedError, match="malformed png"):
        decode(data)


def test_corrupt_pixel_data_is_rejected() -> None:
    ihdr = struct.pack(">IIBBBBB", 2, 2, 16, 2, 0, 0, 0)
    data = (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", ihdr)
        + chunk(b"IDAT", b"this is not zlib data")
        + chunk(b"IEND", b"")
    )
    with pytest.raises(InputRejectedError, match="malformed png"):
        decode(data)


def test_bad_row_filter_is_rejected() -> None:
    ihdr = struct.pack(">IIBBBBB", 1, 1, 16, 2, 0, 0, 0)
    row = b"\x09" + b"\x00" * 6  # filter 9 does not exist
    data = (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", ihdr)
        + chunk(b"IDAT", zlib.compress(row))
        + chunk(b"IEND", b"")
    )
    with pytest.raises(InputRejectedError, match="malformed png"):
        decode(data)


def test_several_idat_chunks_are_joined() -> None:
    row = b"\x00" + struct.pack(">HHH", 0x1234, 0x5678, 0x9ABC)
    compressed = zlib.compress(row)
    ihdr = struct.pack(">IIBBBBB", 1, 1, 16, 2, 0, 0, 0)
    data = (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", ihdr)
        + chunk(b"IDAT", compressed[:5])
        + chunk(b"IDAT", compressed[5:])
        + chunk(b"IEND", b"")
    )
    assert decode(data).pixels == little_endian([0x1234, 0x5678, 0x9ABC])


# ---------------------------------------------------------------------- memory limits


def test_memory_estimate_rejects_before_any_decoding() -> None:
    limits = DecodeLimits(max_input_bytes=10 << 20, max_pixels=10_000_000, max_decoded_bytes=100)
    with pytest.raises(InputRejectedError, match="bytes of memory"):
        decode(png_16(random_samples(13 * 11 * 3, 1), 13, 11, 2), limits)


def test_memory_estimate_counts_the_source_depth() -> None:
    samples = random_samples(4 * 4 * 3, 1)
    allowed = DecodeLimits(10 << 20, 1000, max_decoded_bytes=4 * 4 * 4 * 2)
    assert decode(png_16(samples, 4, 4, 2), allowed).width == 4
    tight = DecodeLimits(10 << 20, 1000, max_decoded_bytes=4 * 4 * 4 * 2 - 1)
    with pytest.raises(InputRejectedError, match="bytes of memory"):
        decode(png_16(samples, 4, 4, 2), tight)


def test_normalize_hdr_checks_memory_before_converting() -> None:
    decoded = decode(png_16(random_samples(4 * 4 * 3, 2), 4, 4, 2))
    tight = DecodeLimits(10 << 20, 1000, max_decoded_bytes=4 * 4 * 3 * 2 - 1)
    with pytest.raises(InputRejectedError, match="limit"):
        normalize_hdr(decoded, tight)
    exact = DecodeLimits(10 << 20, 1000, max_decoded_bytes=4 * 4 * 3 * 2)
    assert normalize_hdr(decoded, exact) is not None


# ------------------------------------------------------------------- colour metadata


def cicp(primaries: int, transfer: int) -> bytes:
    return chunk(b"cICP", bytes([primaries, transfer, 0, 1]))


@pytest.mark.parametrize(
    ("code", "expected"),
    [(13, "srgb"), (1, "bt709"), (16, "pq"), (18, "hlg"), (8, "linear")],
)
def test_transfer_comes_from_cicp(code: int, expected: str) -> None:
    decoded = decode(png_16([1, 2, 3], 1, 1, 2, extra=cicp(1, code)))
    assert decoded.transfer == expected
    assert decoded.colour_notes == ("cicp_signalled",)


@pytest.mark.parametrize(("code", "expected"), [(1, "bt709"), (9, "bt2020"), (12, "p3")])
def test_primaries_come_from_cicp(code: int, expected: str) -> None:
    assert decode(png_16([1, 2, 3], 1, 1, 2, extra=cicp(code, 13))).primaries == expected


def test_no_metadata_is_treated_as_sdr_and_recorded() -> None:
    decoded = decode(png_16([1, 2, 3], 1, 1, 2))
    assert (decoded.transfer, decoded.primaries) == ("srgb", "bt709")
    assert decoded.colour_notes == ("transfer_assumed",)


def test_unknown_transfer_is_treated_as_sdr_and_recorded() -> None:
    decoded = decode(png_16([1, 2, 3], 1, 1, 2, extra=cicp(1, 99)))
    assert decoded.transfer == "srgb"
    assert "transfer_assumed" in decoded.colour_notes


def test_unknown_primaries_are_treated_as_bt709_and_recorded() -> None:
    decoded = decode(png_16([1, 2, 3], 1, 1, 2, extra=cicp(99, 13)))
    assert decoded.primaries == "bt709"
    assert "primaries_assumed" in decoded.colour_notes


def test_malformed_cicp_counts_as_absent() -> None:
    decoded = decode(png_16([1, 2, 3], 1, 1, 2, extra=chunk(b"cICP", b"\x01")))
    assert "transfer_assumed" in decoded.colour_notes


def test_cicp_wins_over_icc_and_that_is_recorded() -> None:
    extra = chunk(b"iCCP", b"name\x00\x00" + zlib.compress(b"not a real profile")) + cicp(9, 16)
    decoded = decode(png_16([1, 2, 3], 1, 1, 2, extra=extra))
    assert decoded.transfer == "pq"
    assert "cicp_preferred_over_icc" in decoded.colour_notes


# ---------------------------------------------------------------- the uint16 view


def test_eight_bit_sources_have_no_hdr_view() -> None:
    decoded = DecodedImage(
        source_sha256="a" * 64,
        media_format="png",
        width=1,
        height=1,
        channels=3,
        bit_depth=8,
        pixels=b"\x01\x02\x03",
        mode="RGB",
    )
    assert normalize_hdr(decoded, LIMITS) is None


def test_hdr_view_has_exact_code_values_and_documents_their_meaning() -> None:
    samples = random_samples(5 * 3 * 3, 3)
    decoded = decode(png_16(samples, 5, 3, 2, extra=cicp(9, 16)))
    view = normalize_hdr(decoded, LIMITS)
    assert view is not None
    assert view.pixels == little_endian(samples)
    assert (view.dtype, view.mode, view.bit_depth) == ("uint16", "RGB;16", 16)
    assert (view.transfer, view.primaries, view.colour_space) == ("pq", "bt2020", "source")


def _orientation_samples() -> list[int]:
    return [
        1000, 1001, 1002, 2000, 2001, 2002, 3000, 3001, 3002,
        4000, 4001, 4002, 5000, 5001, 5002, 6000, 6001, 6002,
    ]  # fmt: skip


@pytest.mark.parametrize(
    ("orientation", "size", "first_pixel"),
    [
        (1, (3, 2), 1000),
        (2, (3, 2), 3000),
        (3, (3, 2), 6000),
        (4, (3, 2), 4000),
        (5, (2, 3), 1000),
        (6, (2, 3), 4000),
        (7, (2, 3), 6000),
        (8, (2, 3), 3000),
    ],
)
def test_hdr_view_applies_orientation_keeping_values_and_channels_together(
    orientation: int, size: tuple[int, int], first_pixel: int
) -> None:
    decoded = dataclasses.replace(
        decode(png_16(_orientation_samples(), 3, 2, 2)), orientation=orientation
    )
    view = normalize_hdr(decoded, LIMITS)
    assert view is not None
    assert (view.width, view.height) == size
    assert struct.unpack("<HHH", view.pixels[:6]) == (first_pixel, first_pixel + 1, first_pixel + 2)
    assert sorted(struct.unpack(f"<{len(view.pixels) // 2}H", view.pixels)) == sorted(
        _orientation_samples()
    )


def test_hdr_view_leaves_the_decoded_image_unchanged() -> None:
    decoded = decode(png_16(random_samples(9, 4), 3, 1, 2))
    snapshot = dataclasses.replace(decoded)
    normalize_hdr(dataclasses.replace(decoded, orientation=3), LIMITS)
    normalize(decoded)
    assert decoded == snapshot


# ------------------------------------------------------------------- the 8-bit derivative


@pytest.mark.parametrize("transfer", ["srgb", "bt709", "pq", "hlg", "linear"])
@pytest.mark.parametrize("bit_depth", [10, 12, 16])
def test_tone_curve_is_monotonic_and_reaches_both_ends(transfer: str, bit_depth: int) -> None:
    curve = tone_curve(transfer, bit_depth)
    assert all(left <= right for left, right in itertools.pairwise(curve))
    assert curve[0] == 0
    assert curve[(1 << bit_depth) - 1] == 255
    assert all(0 <= value <= 255 for value in curve)


def test_tone_curve_is_deterministic() -> None:
    assert tone_curve.__wrapped__("pq", 16) == tone_curve.__wrapped__("pq", 16)


def test_pq_sdr_white_lands_near_the_middle_not_at_the_top() -> None:
    pq_203_nits = int(0.58 * 65535)  # 203 nits is about 58% of the PQ signal range
    value = tone_curve("pq", 16)[pq_203_nits]
    assert 150 <= value <= 215


def test_hlg_is_brighter_than_the_same_signal_in_pq_at_mid_range() -> None:
    assert tone_curve("hlg", 16)[32768] > tone_curve("pq", 16)[20000]


def test_ten_bit_code_values_saturate_above_their_range() -> None:
    curve = tone_curve("srgb", 10)
    assert curve[1023] == 255 and curve[60000] == 255


@pytest.mark.parametrize(
    ("code", "transfer", "expected"),
    [
        ((13, "srgb"), "srgb", "tone_mapped:scale"),
        ((16, "pq"), "pq", "tone_mapped:reinhard:white=203:peak=10000"),
        ((18, "hlg"), "hlg", "tone_mapped:reinhard:white=203:peak=1000"),
        ((8, "linear"), "linear", "tone_mapped:srgb_oetf"),
    ],
)
def test_the_derivative_records_its_operator_and_parameters(
    code: tuple[int, str], transfer: str, expected: str
) -> None:
    decoded = decode(png_16([1000, 20000, 60000], 1, 1, 2, extra=cicp(1, code[0])))
    assert decoded.transfer == transfer
    working = normalize(decoded)
    assert expected in working.transforms
    assert (working.mode, working.dtype) == ("RGB", "uint8")
    assert len(working.pixels) == 3


def test_scaling_is_exact_for_sdr() -> None:
    working = normalize(decode(png_16([0, 32768, 65535], 1, 1, 2, extra=cicp(1, 13))))
    assert list(working.pixels) == [0, 128, 255]


def test_alpha_is_scaled_proportionally_even_for_hdr() -> None:
    decoded = decode(png_16([65535, 65535, 65535, 32768], 1, 1, 6, extra=cicp(1, 16)))
    working = normalize(decoded)
    assert working.mode == "RGBA"
    assert working.pixels[3] == 128
    assert working.pixels[0] == 255


def test_wide_gamut_is_recorded_and_not_claimed_as_srgb() -> None:
    working = normalize(decode(png_16([1000, 2000, 3000], 1, 1, 2, extra=cicp(9, 16))))
    assert working.colour_space == "source"
    assert "gamut_not_converted" in working.transforms


def test_eight_bit_hdr_is_not_claimed_as_srgb() -> None:
    row = b"\x00" + bytes((10, 20, 30))
    ihdr = struct.pack(">IIBBBBB", 1, 1, 8, 2, 0, 0, 0)
    data = (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", ihdr)
        + cicp(1, 16)
        + chunk(b"IDAT", zlib.compress(row))
        + chunk(b"IEND", b"")
    )
    decoded = decode(data)
    assert (decoded.transfer, decoded.bit_depth) == ("pq", 8)
    working = normalize(decoded)
    assert working.colour_space == "source"
    assert "hdr_8_bit_not_tone_mapped" in working.transforms
    assert normalize_hdr(decoded, LIMITS) is None  # only for more than 8 bits


def test_webp_memory_limit_is_checked_before_the_native_decoder_is_built(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fail(*_: object, **__: object) -> None:
        raise AssertionError("Image.open allocates the WebP canvas, so it must not run")

    monkeypatch.setattr(Image, "open", fail)
    canvas = (999).to_bytes(3, "little") + (999).to_bytes(3, "little")  # 1000 x 1000
    body = b"VP8X" + struct.pack("<I", 10) + b"\x00" * 4 + canvas + b"\x00" * 8
    webp = b"RIFF" + struct.pack("<I", 4 + len(body)) + b"WEBP" + body
    limits = DecodeLimits(10 << 20, max_pixels=1_000_000, max_decoded_bytes=1_000_000)
    with pytest.raises(InputRejectedError, match="bytes of memory"):
        decode(webp, limits)


def _eight_bit_png(extra: bytes) -> bytes:
    ihdr = struct.pack(">IIBBBBB", 1, 1, 8, 2, 0, 0, 0)
    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", ihdr)
        + extra
        + chunk(b"IDAT", zlib.compress(b"\x00\x0a\x14\x1e"))
        + chunk(b"IEND", b"")
    )


def test_unconverted_pixels_keep_their_signalled_transfer_and_primaries() -> None:
    working = normalize(decode(_eight_bit_png(cicp(9, 16))))
    assert (working.colour_space, working.transfer, working.primaries) == (
        "source",
        "pq",
        "bt2020",
    )


def test_tone_mapped_wide_gamut_reports_srgb_encoding_with_its_real_primaries() -> None:
    working = normalize(decode(png_16([1000, 2000, 3000], 1, 1, 2, extra=cicp(9, 16))))
    assert (working.transfer, working.primaries) == ("srgb", "bt2020")


def test_tone_mapped_bt709_is_fully_srgb() -> None:
    working = normalize(decode(png_16([1000, 2000, 3000], 1, 1, 2, extra=cicp(1, 16))))
    assert (working.colour_space, working.transfer, working.primaries) == (
        "srgb",
        "srgb",
        "bt709",
    )


def test_cicp_srgb_wins_over_a_conflicting_icc_profile() -> None:
    profile = chunk(b"iCCP", b"p\x00\x00" + zlib.compress(DISPLAY_P3))
    decoded = decode(_eight_bit_png(profile + cicp(1, 13)))
    assert decoded.icc_profile == DISPLAY_P3
    assert "cicp_preferred_over_icc" in decoded.colour_notes
    working = normalize(decoded)
    assert working.pixels == decoded.pixels  # the profile would have changed these
    assert working.colour_space == "srgb"
    assert "icc_converted_to_srgb" not in working.transforms


def test_icc_is_used_when_cicp_is_not_fully_understood() -> None:
    profile = chunk(b"iCCP", b"p\x00\x00" + zlib.compress(DISPLAY_P3))
    decoded = decode(_eight_bit_png(profile + cicp(1, 99)))
    assert "cicp_preferred_over_icc" not in decoded.colour_notes
    assert "icc_converted_to_srgb" in normalize(decoded).transforms


def test_cicp_srgb_is_not_described_as_an_assumption() -> None:
    working = normalize(decode(_eight_bit_png(cicp(1, 13))))
    assert "assumed_srgb" not in working.transforms
    assert "cicp_signalled" in working.transforms


def test_conversion_off_reports_what_the_source_signalled() -> None:
    policy = NormalizationPolicy(convert_to_srgb=False)
    working = normalize(decode(_eight_bit_png(cicp(12, 13))), policy)
    assert (working.colour_space, working.primaries) == ("source", "p3")


def test_the_derivative_keeps_the_assumption_notes() -> None:
    working = normalize(decode(png_16([1, 2, 3], 1, 1, 2)))
    assert "transfer_assumed" in working.transforms


def test_derivative_of_a_sixteen_bit_image_can_use_an_icc_profile() -> None:
    working = normalize(decode(png_16([1000, 20000, 60000], 1, 1, 2)))
    assert working.colour_space == "srgb"  # no profile, so sRGB is assumed
    assert "assumed_srgb" in working.transforms


def test_a_large_gradient_round_trips_quickly() -> None:
    width, height = 256, 256
    samples = [(x * y) & 0xFFFF for y in range(height) for x in range(width) for _ in range(3)]
    data = b"".join(struct.pack(">H", s) for s in samples)
    rows = b"".join(b"\x00" + data[y * width * 6 : (y + 1) * width * 6] for y in range(height))
    ihdr = struct.pack(">IIBBBBB", width, height, 16, 2, 0, 0, 0)
    png = (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", ihdr)
        + chunk(b"IDAT", zlib.compress(rows))
        + chunk(b"IEND", b"")
    )
    assert decode(png).pixels == little_endian(samples)
