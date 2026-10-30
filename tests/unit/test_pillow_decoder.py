"""PillowDecoder against generated images and hostile input. No binary fixtures."""

import io
import random
import struct
import zlib
from pathlib import Path

import pytest
from PIL import Image, ImageCms

from photoproof.contracts.exceptions import InputRejectedError
from photoproof.imaging.adapters.pillow import PillowDecoder
from photoproof.imaging.decoder import DecodedImage, DecodeLimits
from photoproof.imaging.registry import DecoderRegistry, default_decoders

LIMITS = DecodeLimits(max_input_bytes=10 << 20, max_pixels=10_000_000)
SHA = "a" * 64
SIZE = (16, 12)
decoder = PillowDecoder()


def gradient(mode: str = "RGB") -> Image.Image:
    image = Image.new("RGB", SIZE)
    image.putdata([(x * 15, y * 20, (x + y) * 9) for y in range(SIZE[1]) for x in range(SIZE[0])])
    return image.convert(mode)


def with_alpha() -> Image.Image:
    image = gradient("RGBA")  # WebP drops an alpha channel that is fully opaque
    image.putalpha(gradient("L"))
    return image


def encode(image: Image.Image, fmt: str, **options: object) -> bytes:
    buffer = io.BytesIO()
    image.save(buffer, format=fmt, **options)
    return buffer.getvalue()


def decode(data: bytes, limits: DecodeLimits = LIMITS) -> DecodedImage:
    return decoder.decode(data, source_sha256=SHA, limits=limits)


def png_chunk(kind: bytes, body: bytes) -> bytes:
    crc = zlib.crc32(kind + body)
    return struct.pack(">I", len(body)) + kind + body + struct.pack(">I", crc)


def png_header_only(width: int, height: int, *, depth: int = 8, colour: int = 2) -> bytes:
    """A valid PNG header claiming any size, with almost no data behind it."""
    ihdr = struct.pack(">IIBBBBB", width, height, depth, colour, 0, 0, 0)
    return (
        b"\x89PNG\r\n\x1a\n"
        + png_chunk(b"IHDR", ihdr)
        + png_chunk(b"IDAT", zlib.compress(b"\x00"))
        + png_chunk(b"IEND", b"")
    )


# ------------------------------------------------------------------------ valid inputs


@pytest.mark.parametrize(
    ("data", "media_format", "mode", "conversion", "bit_depth", "channels"),
    [
        (encode(gradient(), "JPEG"), "jpeg", "RGB", "none", 8, 3),
        (encode(gradient(), "JPEG", progressive=True), "jpeg", "RGB", "none", 8, 3),
        (encode(gradient("L"), "JPEG"), "jpeg", "RGB", "gray_to_rgb", 8, 3),
        (encode(gradient("CMYK"), "JPEG"), "jpeg", "RGB", "cmyk_to_rgb", 8, 3),
        (encode(gradient(), "PNG"), "png", "RGB", "none", 8, 3),
        (encode(gradient("RGBA"), "PNG"), "png", "RGBA", "none", 8, 4),
        (encode(gradient("L"), "PNG"), "png", "RGB", "gray_to_rgb", 8, 3),
        (encode(gradient("1"), "PNG"), "png", "RGB", "gray_to_rgb", 8, 3),
        (encode(gradient("LA"), "PNG"), "png", "RGBA", "gray_alpha_to_rgba", 8, 4),
        (encode(gradient("P"), "PNG"), "png", "RGB", "palette_to_rgb", 8, 3),
        (encode(gradient("I;16"), "PNG"), "png", "I;16", "none", 16, 1),
        (encode(gradient(), "WEBP", lossless=True), "webp", "RGB", "none", 8, 3),
        (encode(gradient(), "WEBP", quality=80), "webp", "RGB", "none", 8, 3),
        (encode(with_alpha(), "WEBP", lossless=True), "webp", "RGBA", "none", 8, 4),
    ],
)
def test_valid_images_reach_their_documented_working_mode(
    data: bytes, media_format: str, mode: str, conversion: str, bit_depth: int, channels: int
) -> None:
    image = decode(data)
    assert (image.media_format, image.mode, image.conversion) == (media_format, mode, conversion)
    assert (image.bit_depth, image.channels) == (bit_depth, channels)
    assert (image.width, image.height) == SIZE
    assert image.source_sha256 == SHA
    assert len(image.pixels) == SIZE[0] * SIZE[1] * channels * bit_depth // 8


def test_lossless_pixels_are_exact() -> None:
    original = gradient()
    for data in (encode(original, "PNG"), encode(original, "WEBP", lossless=True)):
        assert decode(data).pixels == original.tobytes()


def test_palette_with_transparency_becomes_rgba() -> None:
    image = gradient("P")
    image.info["transparency"] = 0
    assert decode(encode(image, "PNG", transparency=0)).conversion == "palette_to_rgba"


def test_rgb_transparency_chunk_becomes_rgba() -> None:
    data = encode(gradient(), "PNG", transparency=(0, 0, 0))
    decoded = decode(data)
    assert (decoded.mode, decoded.conversion) == ("RGBA", "transparency_to_rgba")


def test_sixteen_bit_gray_keeps_its_depth_and_values() -> None:
    image = Image.new("I;16", (2, 1))
    image.putpixel((0, 0), 1000)
    image.putpixel((1, 0), 65535)
    decoded = decode(encode(image, "PNG"))
    assert decoded.pixels == struct.pack("<HH", 1000, 65535)


def test_sixteen_bit_gray_with_transparency_is_rejected_rather_than_made_opaque() -> None:
    ihdr = struct.pack(">IIBBBBB", 2, 1, 16, 0, 0, 0, 0)
    row = b"\x00" + struct.pack(">HH", 1000, 2000)
    data = (
        b"\x89PNG\r\n\x1a\n"
        + png_chunk(b"IHDR", ihdr)
        + png_chunk(b"tRNS", struct.pack(">H", 1000))
        + png_chunk(b"IDAT", zlib.compress(row))
        + png_chunk(b"IEND", b"")
    )
    with pytest.raises(InputRejectedError, match="16-bit grayscale with transparency"):
        decode(data)


@pytest.mark.parametrize("colour", [2, 4, 6])
def test_sixteen_bit_colour_records_the_loss_of_depth(colour: int) -> None:
    channels = {2: 3, 4: 2, 6: 4}[colour]
    row = b"\x00" + b"\x12\x34" * channels
    ihdr = struct.pack(">IIBBBBB", 1, 1, 16, colour, 0, 0, 0)
    data = (
        b"\x89PNG\r\n\x1a\n"
        + png_chunk(b"IHDR", ihdr)
        + png_chunk(b"IDAT", zlib.compress(row))
        + png_chunk(b"IEND", b"")
    )
    decoded = decode(data)
    assert (decoded.bit_depth, decoded.conversion) == (8, "16_bit_to_8_bit")


def test_input_bytes_are_unchanged_and_never_written_to_disk(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    data = encode(gradient(), "PNG")
    before = bytes(data)
    decode(data)
    assert data == before
    assert list(tmp_path.iterdir()) == []


# -------------------------------------------------------------------------- metadata


def test_icc_profile_is_exposed_without_being_applied() -> None:
    profile = ImageCms.ImageCmsProfile(ImageCms.createProfile("sRGB")).tobytes()
    original = gradient()
    decoded = decode(encode(original, "PNG", icc_profile=profile))
    assert decoded.icc_profile == profile
    assert decoded.pixels == original.tobytes()


def test_missing_icc_profile_is_none() -> None:
    assert decode(encode(gradient(), "PNG")).icc_profile is None


@pytest.mark.parametrize("orientation", range(1, 9))
def test_orientation_is_exposed_without_being_applied(orientation: int) -> None:
    exif = Image.Exif()
    exif[0x0112] = orientation
    decoded = decode(encode(gradient(), "JPEG", exif=exif))
    assert decoded.orientation == orientation
    assert (decoded.width, decoded.height) == SIZE


@pytest.mark.parametrize("value", [0, 9, 300])
def test_invalid_orientation_counts_as_absent(value: int) -> None:
    exif = Image.Exif()
    exif[0x0112] = value
    assert decode(encode(gradient(), "JPEG", exif=exif)).orientation == 1


def test_corrupt_exif_does_not_reject_the_image() -> None:
    assert decode(encode(gradient(), "JPEG", exif=b"Exif\x00\x00not tiff")).orientation == 1


def test_library_versions_name_pillow_and_its_codecs() -> None:
    versions = decoder.library_versions()
    assert versions["Pillow"] and versions["Pillow:jpg"] and versions["Pillow:webp"]


def test_probe_only_claims_its_own_formats() -> None:
    assert decoder.probe(encode(gradient(), "PNG")) == "png"
    assert decoder.probe(b"II*\x00" + b"\x00" * 32) is None
    assert decoder.probe(b"") is None


def test_default_registry_has_this_decoder() -> None:
    assert default_decoders().available() >= {"jpeg", "png", "webp"}


def test_missing_library_leaves_an_empty_registry(monkeypatch: pytest.MonkeyPatch) -> None:
    import sys

    monkeypatch.setitem(sys.modules, "photoproof.imaging.adapters.pillow", None)
    assert default_decoders().available() == frozenset()
    assert isinstance(DecoderRegistry().available(), frozenset)


# ------------------------------------------------------------------- limits and bombs


def _load_must_not_run(monkeypatch: pytest.MonkeyPatch) -> None:
    def fail(_: object) -> None:
        raise AssertionError("pixels were decoded before the size check")

    monkeypatch.setattr(Image.Image, "load", fail)
    monkeypatch.setattr("PIL.ImageFile.ImageFile.load", fail)


@pytest.mark.parametrize(("width", "height"), [(100000, 100000), (4000, 4000), (1, 10_000_001)])
def test_bomb_header_is_rejected_before_any_pixel(
    monkeypatch: pytest.MonkeyPatch, width: int, height: int
) -> None:
    _load_must_not_run(monkeypatch)
    with pytest.raises(InputRejectedError, match="over the limit"):
        decode(png_header_only(width, height))


def _webp_vp8x(width: int, height: int) -> bytes:
    canvas = (width - 1).to_bytes(3, "little") + (height - 1).to_bytes(3, "little")
    body = b"VP8X" + struct.pack("<I", 10) + b"\x00" * 4 + canvas + b"\x00" * 8
    return b"RIFF" + struct.pack("<I", 4 + len(body)) + b"WEBP" + body


def _webp_vp8l(width: int, height: int) -> bytes:
    bits = (width - 1) | ((height - 1) << 14)
    body = b"VP8L" + struct.pack("<I", 5) + b"\x2f" + bits.to_bytes(4, "little") + b"\x00"
    return b"RIFF" + struct.pack("<I", 4 + len(body)) + b"WEBP" + body


def _webp_vp8(width: int, height: int) -> bytes:
    frame = b"\x00\x00\x00\x9d\x01\x2a" + struct.pack("<HH", width, height) + b"\x00" * 8
    body = b"VP8 " + struct.pack("<I", len(frame)) + frame
    return b"RIFF" + struct.pack("<I", 4 + len(body)) + b"WEBP" + body


@pytest.mark.parametrize(
    "bomb",
    [
        _webp_vp8x(1 << 24, 1 << 24),
        _webp_vp8x(16383, 16383),
        _webp_vp8l(16384, 16384),
        _webp_vp8(16383, 16383),
    ],
)
def test_webp_bomb_is_rejected_before_the_native_decoder_is_built(
    monkeypatch: pytest.MonkeyPatch, bomb: bytes
) -> None:
    def fail(*_: object, **__: object) -> None:
        raise AssertionError("Image.open allocates the WebP canvas, so it must not run")

    monkeypatch.setattr(Image, "open", fail)
    with pytest.raises(InputRejectedError, match="over the limit"):
        decode(bomb)


@pytest.mark.parametrize(
    "broken",
    [
        b"RIFF\x10\x00\x00\x00WEBPVP8Z" + b"\x00" * 32,  # unknown chunk
        b"RIFF\x10\x00\x00\x00WEBPVP8L\x05\x00\x00\x00\x00" + b"\x00" * 8,  # bad signature
        b"RIFF\x10\x00\x00\x00WEBPVP8 \x05\x00\x00\x00\x00\x00\x00" + b"\x00" * 8,  # bad start
        b"RIFF\x10\x00\x00\x00WEBPVP8 ",  # no frame header
    ],
)
def test_webp_with_no_valid_header_is_rejected(broken: bytes) -> None:
    with pytest.raises(InputRejectedError, match="no valid image header"):
        decode(broken)


@pytest.mark.parametrize(
    "image",
    [
        encode(gradient(), "WEBP", lossless=True),
        encode(gradient(), "WEBP", quality=80),
        encode(with_alpha(), "WEBP", lossless=True),
        encode(gradient(), "WEBP", icc_profile=b"x" * 16),
    ],
)
def test_real_webp_files_pass_the_hand_written_size_check(image: bytes) -> None:
    decoded = decode(image)
    assert (decoded.width, decoded.height) == SIZE


def test_webp_at_exactly_the_pixel_limit_is_allowed() -> None:
    data = encode(gradient(), "WEBP", lossless=True)
    limits = DecodeLimits(max_input_bytes=10 << 20, max_pixels=SIZE[0] * SIZE[1])
    assert decode(data, limits).width == SIZE[0]
    tight = DecodeLimits(max_input_bytes=10 << 20, max_pixels=SIZE[0] * SIZE[1] - 1)
    with pytest.raises(InputRejectedError, match="over the limit"):
        decode(data, tight)


def test_exactly_at_the_pixel_limit_is_allowed() -> None:
    limits = DecodeLimits(max_input_bytes=10 << 20, max_pixels=SIZE[0] * SIZE[1])
    assert decode(encode(gradient(), "PNG"), limits).width == SIZE[0]


def test_one_pixel_over_the_limit_is_rejected() -> None:
    limits = DecodeLimits(max_input_bytes=10 << 20, max_pixels=SIZE[0] * SIZE[1] - 1)
    with pytest.raises(InputRejectedError, match="over the limit"):
        decode(encode(gradient(), "PNG"), limits)


def test_limit_above_pillows_own_default_is_honoured() -> None:
    limits = DecodeLimits(max_input_bytes=10 << 20, max_pixels=1 << 40)
    with pytest.raises(InputRejectedError, match="malformed png"):
        decode(png_header_only(50000, 50000), limits)  # passes the check, then has no data


def test_pillows_global_limit_is_restored() -> None:
    before = Image.MAX_IMAGE_PIXELS
    decode(encode(gradient(), "PNG"))
    with pytest.raises(InputRejectedError):
        decode(png_header_only(100000, 100000))
    assert before == Image.MAX_IMAGE_PIXELS


def test_input_over_the_byte_limit_is_rejected() -> None:
    limits = DecodeLimits(max_input_bytes=10, max_pixels=1000)
    with pytest.raises(InputRejectedError, match="exceeds 10 bytes"):
        decode(encode(gradient(), "PNG"), limits)


def _animated_webp() -> bytes:
    frames = [gradient(), gradient("L").convert("RGB")]
    return encode(frames[0], "WEBP", save_all=True, append_images=frames[1:], lossless=True)


def test_animation_is_rejected_by_default() -> None:
    with pytest.raises(InputRejectedError, match="2 frames"):
        decode(_animated_webp())


def test_animation_is_allowed_when_frames_are_and_counts_towards_pixels() -> None:
    allowed = DecodeLimits(max_input_bytes=10 << 20, max_pixels=10_000, max_frames=2)
    assert decode(_animated_webp(), allowed).width == SIZE[0]
    pixels = SIZE[0] * SIZE[1]
    tight = DecodeLimits(max_input_bytes=10 << 20, max_pixels=2 * pixels - 1, max_frames=2)
    with pytest.raises(InputRejectedError, match="over the limit"):
        decode(_animated_webp(), tight)


def test_animated_png_is_rejected_by_default() -> None:
    frames = [gradient(), gradient().rotate(180)]
    data = encode(frames[0], "PNG", save_all=True, append_images=frames[1:])
    with pytest.raises(InputRejectedError, match="2 frames"):
        decode(data)


# ------------------------------------------------------------------- malformed inputs


@pytest.mark.parametrize(
    "data",
    [
        b"",
        b"\xff\xd8\xff",
        b"not an image at all, but long enough",
        b"II*\x00" + b"\x00" * 32,  # TIFF, which this decoder does not handle
        b"GIF89a" + b"\x00" * 32,
    ],
)
def test_other_inputs_are_rejected(data: bytes) -> None:
    with pytest.raises(InputRejectedError, match="not a JPEG, PNG or WebP"):
        decode(data)


SAMPLES = {
    "jpeg": encode(gradient(), "JPEG"),
    "progressive": encode(gradient(), "JPEG", progressive=True),
    "png": encode(gradient(), "PNG"),
    "webp": encode(gradient(), "WEBP", lossless=True),
}


@pytest.mark.parametrize("sample", SAMPLES)
@pytest.mark.parametrize("fraction", [0.2, 0.4, 0.6, 0.8, 0.95])
def test_truncated_input_is_rejected_with_no_partial_decode(sample: str, fraction: float) -> None:
    data = SAMPLES[sample]
    with pytest.raises(InputRejectedError):
        decode(data[: int(len(data) * fraction)])


@pytest.mark.parametrize("sample", SAMPLES)
@pytest.mark.parametrize("missing", [1, 2, 4, 12])
def test_losing_only_the_last_bytes_is_still_truncation(sample: str, missing: int) -> None:
    with pytest.raises(InputRejectedError):
        decode(SAMPLES[sample][:-missing])


def test_edited_png_pixel_data_with_a_stale_checksum_is_rejected() -> None:
    """The edit still inflates to the right size, so only the checksum can reveal it."""
    ihdr = struct.pack(">IIBBBBB", 1, 1, 8, 2, 0, 0, 0)
    original = zlib.compress(b"\x00\x10\x20\x30", 0)
    edited = zlib.compress(b"\x00\x10\x20\x31", 0)
    idat = png_chunk(b"IDAT", original)
    forged = edited.join([idat[:8], idat[8 + len(edited) :]])
    data = b"\x89PNG\r\n\x1a\n" + png_chunk(b"IHDR", ihdr) + forged + png_chunk(b"IEND", b"")
    with pytest.raises(InputRejectedError, match="bad checksum"):
        decode(data)


def test_data_after_the_png_end_is_allowed() -> None:
    assert decode(SAMPLES["png"] + b"appended").width == SIZE[0]


def test_png_chunk_claiming_an_impossible_length_is_rejected() -> None:
    data = SAMPLES["png"][:8] + struct.pack(">I", 0xFFFFFFFF) + b"IDAT" + b"\x00" * 32
    with pytest.raises(InputRejectedError, match="truncated"):
        decode(data)


def test_corrupt_png_header_is_rejected() -> None:
    data = bytearray(SAMPLES["png"])
    data[16] ^= 0xFF  # inside IHDR, so its checksum no longer matches
    with pytest.raises(InputRejectedError, match="malformed"):
        decode(bytes(data))


def test_png_with_corrupt_pixel_data_is_rejected() -> None:
    ihdr = struct.pack(">IIBBBBB", 4, 4, 8, 2, 0, 0, 0)
    data = (
        b"\x89PNG\r\n\x1a\n"
        + png_chunk(b"IHDR", ihdr)
        + png_chunk(b"IDAT", b"this is not zlib data")
        + png_chunk(b"IEND", b"")
    )
    with pytest.raises(InputRejectedError, match="malformed"):
        decode(data)


@pytest.mark.parametrize("dimensions", [(0, 0), (0, 10), (10, 0)])
def test_zero_dimensions_are_rejected(dimensions: tuple[int, int]) -> None:
    with pytest.raises(InputRejectedError):
        decode(png_header_only(*dimensions))


def test_signature_of_one_format_with_the_body_of_another_is_rejected() -> None:
    forged = SAMPLES["jpeg"][:3] + SAMPLES["png"]
    with pytest.raises(InputRejectedError):
        decode(forged)


@pytest.mark.parametrize("sample", SAMPLES)
def test_randomly_corrupted_input_only_ever_raises_input_rejected(sample: str) -> None:
    generator = random.Random(f"{sample}-20261030")  # noqa: S311 - reproducible test data
    original = SAMPLES[sample]
    for _ in range(150):
        data = bytearray(original)
        for _ in range(generator.randint(1, 8)):
            data[generator.randrange(len(data))] = generator.randrange(256)
        try:
            decoded = decode(bytes(data))
        except InputRejectedError:
            continue
        assert decoded.width > 0 and decoded.height > 0
