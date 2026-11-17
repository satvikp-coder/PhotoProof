"""The image run-input contract end to end: ingest → resolve once → read-only arrays.

Covers the media inputs: ``input.original_bytes`` (raw stored bytes), ``image.decoded``
(pixels as decoded) and ``image.working`` (normalized working image). Plugins that
declare them receive read-only NumPy arrays, and a decode failure skips the affected
plugins with the reason recorded instead of failing the whole run.
"""

import json
from collections.abc import Callable
from pathlib import Path

import numpy as np
from PIL import Image

from photoproof.contracts.analysis import AnalysisRequest, AnalysisResult
from photoproof.contracts.enums import RunStatus
from photoproof.core.bootstrap import Application
from photoproof.core.controller import AnalysisController
from photoproof.imaging.decoder import DecodedImage, DecodeLimits, ImageDecoder
from photoproof.imaging.registry import DecoderRegistry

Factory = Callable[..., Application]


class _CrashingDecoder(ImageDecoder):
    """A decoder whose child segfaults, used to prove the parent process survives."""

    formats = frozenset({"png"})

    def library_versions(self) -> dict[str, str]:
        return {"crash": "1.0"}

    def probe(self, data: bytes) -> str | None:
        return "png" if data.startswith(b"\x89PNG\r\n\x1a\n") else None

    def decode(self, data: bytes, *, source_sha256: str, limits: DecodeLimits) -> DecodedImage:
        import os

        os._exit(1)  # terminate the child process immediately (simulates a crash)
        raise AssertionError("unreachable")  # pragma: no cover


def _make_png(path: Path) -> Path:
    pixels = np.zeros((8, 6, 3), dtype=np.uint8)
    pixels[..., 0] = 200
    pixels[..., 1] = 100
    Image.fromarray(pixels, "RGB").save(path)
    return path


def _analyze_ingested(app: Application, path: Path) -> AnalysisResult:
    controller = AnalysisController(app)
    target, _record = app.ingestion().ingest_path(path, role="target")
    return controller.analyze(AnalysisRequest(target=target, use_cache=False))


def test_declared_media_inputs_are_resolved_and_read_only(
    make_app: Factory, tmp_path: Path
) -> None:
    app = make_app("mockplugins.imageprobe")
    png = _make_png(tmp_path / "photo.png")
    result = _analyze_ingested(app, png)
    assert result.status is RunStatus.COMPLETED
    states = {p.plugin_id: p.state for p in result.plugins}
    assert states == {
        "imageprobe.mean": "SUCCESS",
        "imageprobe.original": "SUCCESS",
        "imageprobe.working": "SUCCESS",
    }
    assert result.outputs["imageprobe.shape"] == [8, 6, 3]
    assert result.outputs["imageprobe.read_only"] is True
    assert float(result.outputs["imageprobe.mean"]) > 0.0
    assert result.outputs["imageprobe.original_size"] == png.stat().st_size
    assert result.outputs["imageprobe.working_shape"] == [8, 6, 3]
    assert result.outputs["imageprobe.working_read_only"] is True


def test_decode_failure_skips_only_the_pixel_dependents(make_app: Factory, tmp_path: Path) -> None:
    app = make_app("mockplugins.imageprobe")
    app.decoders = DecoderRegistry()  # no decoder for any format on this installation
    result = _analyze_ingested(app, _make_png(tmp_path / "photo.png"))
    assert result.status is RunStatus.INCOMPLETE
    by_id = {p.plugin_id: p for p in result.plugins}
    for pid in ("imageprobe.mean", "imageprobe.working"):
        assert by_id[pid].state == "SKIPPED"
        assert by_id[pid].failure_category == "dependency_unavailable"
        assert by_id[pid].message and "unavailable" in by_id[pid].message
    # The plugin that only needs the stored bytes still runs.
    assert by_id["imageprobe.original"].state == "SUCCESS"
    assert result.outputs["imageprobe.original_size"] > 0


def test_plugins_without_media_inputs_are_unaffected(make_app: Factory, tmp_path: Path) -> None:
    """The demo plugins declare only descriptor inputs; the run is unchanged."""
    app = make_app()
    result = _analyze_ingested(app, _make_png(tmp_path / "photo.png"))
    assert result.status is RunStatus.COMPLETED
    assert result.outputs  # the demo chain produced its outputs


def test_normal_analysis_uses_isolated_child_process(make_app: Factory, tmp_path: Path) -> None:
    """A normal controller analysis decodes in a child process (isolation=process).

    The manifest records the effective isolation mode and the decoded pixels are real,
    proving decoding happened (in a child) rather than being skipped or faked.
    """
    app = make_app("mockplugins.imageprobe")
    png = _make_png(tmp_path / "photo.png")
    result = _analyze_ingested(app, png)
    assert result.status is RunStatus.COMPLETED

    manifest = json.loads(Path(result.manifest_path).read_text())
    assert manifest["software"]["image_isolation"] == "process"

    states = {p.plugin_id: p.state for p in result.plugins}
    assert states == {
        "imageprobe.mean": "SUCCESS",
        "imageprobe.original": "SUCCESS",
        "imageprobe.working": "SUCCESS",
    }
    assert result.outputs["imageprobe.shape"] == [8, 6, 3]
    assert float(result.outputs["imageprobe.mean"]) > 0.0


def test_crashing_decoder_skips_pixel_plugins_and_parent_survives(
    make_app: Factory, tmp_path: Path
) -> None:
    """A crashing decoder is contained in the child; the parent process survives.

    The run is INCOMPLETE, pixel-dependent plugins are skipped, and no clean forensic
    verdict is produced.
    """
    app = make_app("mockplugins.imageprobe")
    app.decoders = DecoderRegistry()
    app.decoders.register(_CrashingDecoder())
    png = _make_png(tmp_path / "photo.png")
    target, _record = app.ingestion().ingest_path(png, role="target")
    controller = AnalysisController(app)
    result = controller.analyze(AnalysisRequest(target=target, use_cache=False))

    # The parent process is still alive: the test reached this point normally.
    assert result.status is RunStatus.INCOMPLETE
    by_id = {p.plugin_id: p for p in result.plugins}
    for pid in ("imageprobe.mean", "imageprobe.working"):
        assert by_id[pid].state == "SKIPPED"
        assert by_id[pid].failure_category == "dependency_unavailable"
        assert by_id[pid].message and "unavailable" in by_id[pid].message
    # The plugin that only needs the stored bytes still runs.
    assert by_id["imageprobe.original"].state == "SUCCESS"
    assert result.outputs["imageprobe.original_size"] > 0
    assert "verdict" not in result.model_dump()
