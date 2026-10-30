"""Format signatures, the decoder registry and supported_formats enforcement."""

import logging
import random
from collections.abc import Callable, Iterator
from pathlib import Path

import pytest

from photoproof.contracts.exceptions import InputRejectedError
from photoproof.core.bootstrap import Application
from photoproof.core.database.store import Database
from photoproof.core.health.checks import check_imaging_codecs
from photoproof.core.storage import evidence as evidence_module
from photoproof.core.storage.evidence import EvidenceStore
from photoproof.core.storage.store import RuntimeStorage
from photoproof.imaging.decoder import DecodedImage, DecodeLimits
from photoproof.imaging.formats import FORMATS, detect_format, format_from_name
from photoproof.imaging.registry import DecoderRegistry

PAD = b"\x00" * 32


def ftyp(major: bytes, *compatible: bytes, size: int | None = None) -> bytes:
    body = major + b"\x00\x00\x00\x00" + b"".join(compatible)
    length = 8 + len(body) if size is None else size
    return length.to_bytes(4, "big") + b"ftyp" + body + PAD


HEADERS = {
    "jpeg": b"\xff\xd8\xff\xe0" + PAD,
    "png": b"\x89PNG\r\n\x1a\n" + PAD,
    "webp": b"RIFF\x10\x00\x00\x00WEBPVP8 " + PAD,
    "jxl codestream": b"\xff\x0a" + PAD,
    "jxl container": bytes.fromhex("0000000C4A584C200D0A870A") + PAD,
    "tiff little-endian": b"II*\x00" + PAD,
    "tiff big-endian": b"MM\x00*" + PAD,
    "heic": ftyp(b"heic", b"mif1", b"heic"),
    "heix": ftyp(b"heix"),
    "mif1 alone": ftyp(b"mif1"),
    "mif1 with heic": ftyp(b"mif1", b"heic"),
    "unknown major, mif1 compatible": ftyp(b"abcd", b"mif1"),
    "avif": ftyp(b"avif", b"mif1", b"miaf"),
    "avis": ftyp(b"avis"),
    "mif1 major with avif compatible": ftyp(b"mif1", b"avif"),
}
EXPECTED = {
    "jpeg": "jpeg",
    "png": "png",
    "webp": "webp",
    "jxl codestream": "jxl",
    "jxl container": "jxl",
    "tiff little-endian": "tiff",
    "tiff big-endian": "tiff",
    "heic": "heif",
    "heix": "heif",
    "mif1 alone": "heif",
    "mif1 with heic": "heif",
    "unknown major, mif1 compatible": "heif",
    "avif": "avif",
    "avis": "avif",
    "mif1 major with avif compatible": "avif",
}


@pytest.mark.parametrize("name", HEADERS)
def test_signatures_are_identified(name: str) -> None:
    assert detect_format(HEADERS[name]) == EXPECTED[name]


def test_all_seven_formats_are_covered() -> None:
    assert set(EXPECTED.values()) == set(FORMATS)


@pytest.mark.parametrize(
    "header",
    [
        b"",
        b"\xff",
        b"\xff\xd8\xff",  # a signature, but shorter than the minimum
        b"\x89PNG\r\n\x1a",
        b"RIFF\x00\x00\x00\x00WEB",
        b"\x00\x00\x00\x10ftyp",
        b"GIF89a" + PAD,
        b"%PDF-1.7" + PAD,
        b"BM" + PAD,
        b"RIFF\x10\x00\x00\x00WAVEfmt " + PAD,  # RIFF, but not WebP
        ftyp(b"isom", b"mp42"),  # an MP4 video
        b"\x00" * 64,
    ],
)
def test_unknown_or_truncated_headers_return_none(header: bytes) -> None:
    assert detect_format(header) is None


@pytest.mark.parametrize("size", [0, 1, 8, 15])
def test_odd_ftyp_sizes_do_not_crash(size: int) -> None:
    detect_format(ftyp(b"abcd", b"mif1", size=size))


def test_ftyp_size_of_zero_means_to_end_of_file() -> None:
    assert detect_format(ftyp(b"abcd", b"mif1", size=0)) == "heif"


def test_ftyp_too_small_for_its_brands_is_not_a_format() -> None:
    assert detect_format(ftyp(b"heic", size=1)) is None


def test_brand_outside_the_declared_box_is_ignored() -> None:
    assert detect_format(ftyp(b"abcd", b"xxxx", b"mif1", size=20)) is None


def test_only_the_first_64_bytes_are_examined() -> None:
    assert detect_format(ftyp(b"abcd", *[b"xxxx"] * 20, b"mif1", size=500)) is None


def test_polyglot_gets_the_first_match_in_the_documented_order() -> None:
    assert detect_format(b"\xff\xd8\xff" + b"\x89PNG\r\n\x1a\n" + PAD) == "jpeg"


def test_random_input_never_raises() -> None:
    generator = random.Random(20261030)  # noqa: S311 - reproducible test data
    for _ in range(20000):
        size = generator.randint(0, 64)
        result = detect_format(generator.randbytes(size))
        assert result is None or result in FORMATS


def test_random_data_after_a_valid_ftyp_box_never_raises() -> None:
    generator = random.Random(20261031)  # noqa: S311 - reproducible test data
    for _ in range(5000):
        tail = generator.randbytes(generator.randint(0, 56))
        size = generator.randint(0, 2**32 - 1).to_bytes(4, "big")
        detect_format(size + b"ftyp" + tail)


def test_bytes_like_input_is_accepted() -> None:
    assert detect_format(bytearray(HEADERS["png"])) == "png"
    assert detect_format(memoryview(HEADERS["png"])) == "png"


@pytest.mark.parametrize(
    ("name", "expected"),
    [("a.JPG", "jpeg"), ("a.tar.png", "png"), ("a.heic", "heif"), ("a", None), ("a.txt", None)],
)
def test_format_from_name(name: str, expected: str | None) -> None:
    assert format_from_name(name) == expected


# ------------------------------------------------------------------------- registry


class _Decoder:
    def __init__(self, *formats: str, library: str = "lib") -> None:
        self.formats = frozenset(formats)
        self._library = library

    def library_versions(self) -> dict[str, str]:
        return {self._library: "1.0"}

    def probe(self, data: bytes) -> str | None:
        return None

    def decode(self, data: bytes, *, source_sha256: str, limits: DecodeLimits) -> DecodedImage:
        raise NotImplementedError


def test_registry_finds_a_decoder_and_reports_availability() -> None:
    registry = DecoderRegistry()
    decoder = _Decoder("jpeg", "png")
    registry.register(decoder)
    assert registry.for_format("png") is decoder
    assert registry.available() == {"jpeg", "png"}
    assert registry.library_versions() == {"lib": "1.0"}


def test_missing_decoder_names_what_to_install() -> None:
    with pytest.raises(InputRejectedError, match=r"unsupported on this platform.*pillow-heif"):
        DecoderRegistry().for_format("heif")


def test_missing_lists_only_accepted_formats_without_a_decoder() -> None:
    registry = DecoderRegistry()
    registry.register(_Decoder("jpeg"))
    assert registry.missing(["jpeg", "heif"]) == {"heif": "pillow-heif"}


def test_two_decoders_for_one_format_is_an_error() -> None:
    registry = DecoderRegistry()
    registry.register(_Decoder("jpeg", "png"))
    with pytest.raises(ValueError, match="already has a decoder"):
        registry.register(_Decoder("png", "webp"))
    assert registry.available() == {"jpeg", "png"}  # the failed registration changed nothing


def test_unknown_format_is_an_error() -> None:
    with pytest.raises(ValueError, match="unknown image format"):
        DecoderRegistry().register(_Decoder("gif"))


def test_library_versions_combine_across_decoders() -> None:
    registry = DecoderRegistry()
    registry.register(_Decoder("jpeg", library="a"))
    registry.register(_Decoder("heif", library="b"))
    assert registry.library_versions() == {"a": "1.0", "b": "1.0"}


# ------------------------------------------------------------------------ ingestion


@pytest.fixture
def store(tmp_path: Path) -> Iterator[EvidenceStore]:
    storage = RuntimeStorage(tmp_path / "ARE")
    storage.ensure_layout()
    database = Database.open(storage.root / "database" / "x.db")
    yield EvidenceStore(storage, database, max_input_bytes=4096, accepted_formats=("jpeg", "png"))
    database.close()


def test_ingest_labels_the_format_from_content_not_the_name(store: EvidenceStore) -> None:
    record = store.ingest(HEADERS["jpeg"], role="target", original_name="lies.png")
    assert record.media_format == "jpeg"


def test_disabled_format_is_rejected_and_not_stored(store: EvidenceStore, tmp_path: Path) -> None:
    with pytest.raises(InputRejectedError, match="format webp is not accepted"):
        store.ingest(HEADERS["webp"], role="target")
    assert list((tmp_path / "ARE" / "objects").glob("??/*")) == []
    assert list((tmp_path / "ARE" / "tmp").iterdir()) == []


def test_unidentified_content_is_stored_with_no_format(store: EvidenceStore) -> None:
    assert store.ingest(b"not an image at all", role="target").media_format is None


def test_header_spanning_read_chunks_is_still_identified(
    store: EvidenceStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(evidence_module, "_CHUNK", 3)
    assert store.ingest(HEADERS["png"], role="target").media_format == "png"


def test_extension_mismatch_is_logged_and_accepted(
    store: EvidenceStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    notes: list[str] = []
    monkeypatch.setattr(
        evidence_module.LOGGER, "info", lambda message, *args: notes.append(message % args)
    )
    store.ingest(HEADERS["jpeg"], role="target", original_name="patient-confidential.png")
    assert len(notes) == 1 and "png" in notes[0] and "jpeg" in notes[0]
    assert "patient-confidential" not in notes[0]  # the submitted name is never logged


def test_matching_extension_logs_nothing(
    store: EvidenceStore, caplog: pytest.LogCaptureFixture
) -> None:
    with caplog.at_level(logging.INFO):
        store.ingest(HEADERS["jpeg"], role="target", original_name="photo.jpg")
    assert not [r for r in caplog.records if "signature" in r.getMessage()]


# --------------------------------------------------------------------------- health


def test_doctor_lists_accepted_formats_without_a_decoder(
    make_app: Callable[..., Application],
) -> None:
    result = check_imaging_codecs(make_app())
    assert result.severity.value == "warning"
    assert "jpeg" in result.message and any("Pillow" in d for d in result.details)


def test_doctor_is_quiet_when_every_format_has_a_decoder(
    make_app: Callable[..., Application],
) -> None:
    app = make_app()
    app.decoders.register(_Decoder("jpeg", "png", "webp"))
    assert check_imaging_codecs(app).ok


def test_decode_limits_come_from_settings(make_app: Callable[..., Application]) -> None:
    app = make_app()
    limits = app.decode_limits()
    assert limits.max_input_bytes == app.settings.resources.max_input_bytes
    assert limits.max_pixels == app.settings.resources.max_image_pixels
