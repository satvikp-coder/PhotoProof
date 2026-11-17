"""Tests for photoproof.imaging.isolation (#9).

Tests the IsolatedDecoder wrapper: in-process mode (direct delegation) and
process mode (child process with resource limits), including crash, timeout
and memory-loss handling.
"""

import contextlib
import io

import pytest
from PIL import Image

from photoproof.contracts.exceptions import (
    DecoderCrashed,
    DecoderError,
    DecoderMemoryLimit,
    DecoderTimeout,
    InputRejectedError,
)
from photoproof.imaging.adapters.pillow import PillowDecoder
from photoproof.imaging.decoder import DecodedImage, DecodeLimits, ImageDecoder
from photoproof.imaging.isolation import IsolatedDecoder

SHA = "a" * 64


def _tiny_png() -> bytes:
    """A minimal valid PNG: 1x1 white pixel, generated via Pillow."""
    buffer = io.BytesIO()
    Image.new("RGB", (1, 1), (255, 255, 255)).save(buffer, format="PNG")
    return buffer.getvalue()


class _FailingAdapter(ImageDecoder):
    """Adapter whose decode always raises InputRejectedError."""

    formats = frozenset({"test"})

    def library_versions(self) -> dict[str, str]:
        return {"test": "1.0"}

    def probe(self, data: bytes) -> str | None:
        return "test" if data else None

    def decode(self, data: bytes, *, source_sha256: str, limits: DecodeLimits) -> DecodedImage:
        raise InputRejectedError("adapter failure")


class _SegfaultAdapter(ImageDecoder):
    """Adapter whose decode segfaults via ctypes."""

    formats = frozenset({"test"})

    def library_versions(self) -> dict[str, str]:
        return {"test": "1.0"}

    def probe(self, data: bytes) -> str | None:
        return "test" if data else None

    def decode(self, data: bytes, *, source_sha256: str, limits: DecodeLimits) -> DecodedImage:
        import ctypes

        ctypes.string_at(0)  # segfault
        raise AssertionError("unreachable")  # pragma: no cover


class _SlowAdapter(ImageDecoder):
    """Adapter whose decode sleeps forever."""

    formats = frozenset({"test"})

    def library_versions(self) -> dict[str, str]:
        return {"test": "1.0"}

    def probe(self, data: bytes) -> str | None:
        return "test" if data else None

    def decode(self, data: bytes, *, source_sha256: str, limits: DecodeLimits) -> DecodedImage:
        import time

        time.sleep(100)
        raise AssertionError("unreachable")  # pragma: no cover


class _MemoryBombAdapter(ImageDecoder):
    """Adapter whose decode raises MemoryError to simulate memory exhaustion."""

    formats = frozenset({"test"})

    def library_versions(self) -> dict[str, str]:
        return {"test": "1.0"}

    def probe(self, data: bytes) -> str | None:
        return "test" if data else None

    def decode(self, data: bytes, *, source_sha256: str, limits: DecodeLimits) -> DecodedImage:
        raise MemoryError("simulated memory exhaustion")  # pragma: no cover


# ------------------------------------------------------------------ in-process mode


def test_in_process_delegates_to_adapter() -> None:
    """isolation=in_process just calls the adapter directly."""
    adapter = PillowDecoder()
    decoder = IsolatedDecoder(adapter, isolation="in_process")
    result = decoder.decode(
        _tiny_png(),
        source_sha256=SHA,
        limits=DecodeLimits(
            max_input_bytes=len(_tiny_png()),
            max_pixels=1,
            timeout_seconds=5,
            max_decoded_bytes=1024,
        ),
    )
    assert result.width == 1
    assert result.height == 1


def test_in_process_rejects_bad_input() -> None:
    """in-process mode still rejects invalid input."""
    adapter = PillowDecoder()
    decoder = IsolatedDecoder(adapter, isolation="in_process")
    with pytest.raises(InputRejectedError, match="not a JPEG, PNG or WebP"):
        decoder.decode(
            b"not an image",
            source_sha256=SHA,
            limits=DecodeLimits(
                max_input_bytes=100,
                max_pixels=1,
                timeout_seconds=5,
                max_decoded_bytes=1024,
            ),
        )


# ------------------------------------------------------------------ process mode


def test_process_mode_delegates_to_adapter() -> None:
    """isolation=process decodes through a child process."""
    adapter = PillowDecoder()
    decoder = IsolatedDecoder(adapter, isolation="process")
    result = decoder.decode(
        _tiny_png(),
        source_sha256=SHA,
        limits=DecodeLimits(
            max_input_bytes=len(_tiny_png()),
            max_pixels=1,
            timeout_seconds=30,
            max_decoded_bytes=1024,
        ),
    )
    assert result.width == 1
    assert result.height == 1


def test_process_mode_rejects_bad_input() -> None:
    """process mode still rejects invalid input."""
    adapter = PillowDecoder()
    decoder = IsolatedDecoder(adapter, isolation="process")
    with pytest.raises(InputRejectedError, match="not a JPEG, PNG or WebP"):
        decoder.decode(
            b"not an image",
            source_sha256=SHA,
            limits=DecodeLimits(
                max_input_bytes=100,
                max_pixels=1,
                timeout_seconds=30,
                max_decoded_bytes=1024,
            ),
        )


def test_process_mode_reports_crash() -> None:
    """A crashing adapter produces DecoderCrashed in process mode."""
    adapter = _SegfaultAdapter()
    decoder = IsolatedDecoder(adapter, isolation="process")
    with pytest.raises(DecoderCrashed):
        decoder.decode(
            b"data",
            source_sha256=SHA,
            limits=DecodeLimits(
                max_input_bytes=4,
                max_pixels=1,
                timeout_seconds=30,
                max_decoded_bytes=1024,
            ),
        )


def test_process_mode_reports_timeout() -> None:
    """A hanging adapter produces DecoderTimeout in process mode."""
    adapter = _SlowAdapter()
    decoder = IsolatedDecoder(adapter, isolation="process")
    with pytest.raises(DecoderTimeout):
        decoder.decode(
            b"data",
            source_sha256=SHA,
            limits=DecodeLimits(
                max_input_bytes=4,
                max_pixels=1,
                timeout_seconds=2,
                max_decoded_bytes=1024,
            ),
        )


def test_process_mode_reports_memory_limit() -> None:
    """A memory-bomb adapter produces DecoderMemoryLimit in process mode."""
    adapter = _MemoryBombAdapter()
    decoder = IsolatedDecoder(adapter, isolation="process")
    with pytest.raises(DecoderMemoryLimit):
        decoder.decode(
            b"data",
            source_sha256=SHA,
            limits=DecodeLimits(
                max_input_bytes=4,
                max_pixels=1,
                timeout_seconds=30,
                max_decoded_bytes=1024,
            ),
        )


def test_process_mode_isolation_error_is_decoder_error() -> None:
    """All decoder errors are DecoderError subclasses."""
    adapter = _SegfaultAdapter()
    decoder = IsolatedDecoder(adapter, isolation="process")
    with pytest.raises(DecoderError):
        decoder.decode(
            b"data",
            source_sha256=SHA,
            limits=DecodeLimits(
                max_input_bytes=4,
                max_pixels=1,
                timeout_seconds=30,
                max_decoded_bytes=1024,
            ),
        )


def test_no_orphan_after_crash() -> None:
    """After a crashed decode, no child process remains alive."""
    import multiprocessing as mp

    adapter = _SegfaultAdapter()
    decoder = IsolatedDecoder(adapter, isolation="process")
    with contextlib.suppress(DecoderCrashed):
        decoder.decode(
            b"data",
            source_sha256=SHA,
            limits=DecodeLimits(
                max_input_bytes=4,
                max_pixels=1,
                timeout_seconds=30,
                max_decoded_bytes=1024,
            ),
        )

    # Give the OS a moment to reap, then check for lingering child processes.
    active_children = mp.active_children()
    assert len(active_children) == 0, f"orphan processes remain: {active_children}"


# ------------------------------------------------------------------ adapter pass-through


def test_library_versions_delegated() -> None:
    """library_versions is delegated to the adapter."""
    adapter = PillowDecoder()
    decoder = IsolatedDecoder(adapter, isolation="process")
    versions = decoder.library_versions()
    assert "Pillow" in versions


def test_probe_delegated() -> None:
    """probe is delegated to the adapter."""
    adapter = PillowDecoder()
    decoder = IsolatedDecoder(adapter, isolation="process")
    assert decoder.probe(_tiny_png()) == "png"
    assert decoder.probe(b"") is None
