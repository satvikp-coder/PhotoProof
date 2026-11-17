"""Safe preview from actual decoded evidence through the controller."""

import hashlib
import io
import json
from collections.abc import Callable
from pathlib import Path

import numpy as np
from PIL import Image, PngImagePlugin

from photoproof.contracts.analysis import AnalysisRequest
from photoproof.core.bootstrap import Application
from photoproof.core.controller import AnalysisController


def test_preview_removes_metadata_and_preserves_evidence(
    make_app: Callable[..., Application], tmp_path: Path
) -> None:
    app = make_app("photoproof.plugins")
    source = tmp_path / "private.png"
    image = Image.fromarray(np.full((32, 48, 3), 120, dtype=np.uint8))
    info = PngImagePlugin.PngInfo()
    info.add_text("XML:com.adobe.xmp", "private-location")
    exif = Image.Exif()
    exif[315] = "private-author"
    image.save(source, pnginfo=info, exif=exif)
    original = source.read_bytes()
    target, _ = app.ingestion().ingest_path(source, role="target")
    controller = AnalysisController(app)
    request = AnalysisRequest(target=target, use_cache=False)
    first = controller.analyze(request)
    second = controller.analyze(request)
    states = {p.plugin_id: p.state for p in first.plugins}
    assert states["support.preview"] == "SUCCESS", first.plugins
    ref = first.outputs["support.preview"]["artifact"]
    assert second.outputs["support.preview"] == first.outputs["support.preview"]
    data = app.storage.blob_path("artifacts", ref["sha256"]).read_bytes()
    assert hashlib.sha256(data).hexdigest() == ref["sha256"]
    assert b"private-location" not in data and b"private-author" not in data
    with Image.open(io.BytesIO(data)) as preview:
        assert not preview.info and not preview.getexif()
        assert preview.size == (48, 32)
    manifest = app.storage.read_json("manifests", Path(first.manifest_path).name)
    assert ref["sha256"] in json.dumps(manifest)
    assert first.outputs["support.preview_info"]["kind"] == "derived_preview"
    assert first.outputs["support.preview_info"]["evidence"] is False
    assert source.read_bytes() == original
    assert app.storage.blob_path("objects", target.sha256).read_bytes() == original
