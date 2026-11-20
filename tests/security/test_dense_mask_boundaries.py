"""Unavailable provenance and resource failures cannot publish an empty successful mask."""

import json

import numpy as np
import pytest
from PIL import Image

from photoproof.contracts.analysis import AnalysisRequest
from photoproof.contracts.copymove import VerificationBindings
from photoproof.core.controller import AnalysisController


@pytest.mark.parametrize("fault", ["source", "frame", "digest", "budget"])
def test_mask_rejects_bad_bindings_or_budget(make_app, app_root, tmp_path, monkeypatch, fault):
    if fault == "budget":
        (app_root / "configs" / "plugins" / "copymove.mask.yaml").write_text(
            "max_pixel_work: 1\n", encoding="utf-8"
        )
    else:
        original = VerificationBindings.to_json

        def changed(self):
            data = json.loads(original(self))
            if fault == "source":
                data["source_sha256"] = "b" * 64
            elif fault == "frame":
                data["image_size"] = [513, 512]
            else:
                data["matches_sha256"] = "b" * 64
            return json.dumps(data)

        monkeypatch.setattr(VerificationBindings, "to_json", changed)
    image = np.full((512, 512, 3), 120, np.uint8)
    patch = np.random.default_rng(22).integers(0, 256, (96, 96, 3), np.uint8)
    image[32:128, 32:128] = patch
    image[240:336, 240:336] = patch
    path = tmp_path / "synthetic.png"
    Image.fromarray(image).save(path)
    app = make_app("photoproof.plugins")
    target = app.ingestion().ingest_path(path, role="target")[0]
    result = AnalysisController(app).analyze(AnalysisRequest(target=target, use_cache=False))
    states = {p.plugin_id: p.state for p in result.plugins}
    assert states["copymove.verification"] == "SUCCESS"
    assert states["copymove.mask"] == "FAILED", result.plugins
    assert not any(
        name in result.outputs
        for name in (
            "copymove.mask",
            "copymove.mask_metadata",
            "copymove.correlation_map",
            "copymove.correlation_validity",
            "copymove.regions",
            "copymove.mask_stats",
        )
    )
    assert app.storage.blob_path("objects", target.sha256).read_bytes() == path.read_bytes()
