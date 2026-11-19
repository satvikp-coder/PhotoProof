"""Bad provenance or exhausted budgets fail without zero-score evidence."""

import numpy as np
import pytest
from PIL import Image

from photoproof.contracts.analysis import AnalysisRequest
from photoproof.contracts.copymove import Clusters
from photoproof.core.controller import AnalysisController


@pytest.mark.parametrize(
    "fault", ["source", "frame", "parent_count", "ransac_budget", "texture_budget"]
)
def test_bad_verification_inputs_publish_no_verified_result(
    make_app, app_root, tmp_path, monkeypatch, fault
):
    if fault in {"source", "frame", "parent_count"}:
        writer = Clusters.to_json

        def bad_metadata(self):
            if fault == "source":
                update = {"source_sha256": "b" * 64}
            elif fault == "frame":
                update = {"image_size": (513, 512)}
            else:
                update = {"num_matches": self.num_matches + 1}
            return writer(self.model_copy(update=update))

        monkeypatch.setattr(Clusters, "to_json", bad_metadata)
    else:
        option = "max_ransac_work" if fault == "ransac_budget" else "max_texture_work"
        (app_root / "configs" / "plugins" / "copymove.verification.yaml").write_text(
            option + ": 1\n", encoding="utf-8"
        )
    pixels = np.full((512, 512, 3), 120, np.uint8)
    patch = np.random.default_rng(22).integers(0, 256, (96, 96, 3), np.uint8)
    pixels[32:128, 32:128] = patch
    pixels[240:336, 240:336] = patch
    path = tmp_path / "controlled.png"
    Image.fromarray(pixels).save(path)
    original = path.read_bytes()
    app = make_app("photoproof.plugins")
    target = app.ingestion().ingest_path(path, role="target")[0]
    result = AnalysisController(app).analyze(AnalysisRequest(target=target, use_cache=False))
    states = {p.plugin_id: p.state for p in result.plugins}
    assert states["copymove.clustering"] == "SUCCESS"
    assert states["copymove.verification"] == "FAILED"
    for name in (
        "copymove.verified",
        "copymove.verified_inliers",
        "copymove.verification_bindings",
        "copymove.verification_details",
        "copymove.verification_stats",
    ):
        assert name not in result.outputs
    assert app.storage.blob_path("objects", target.sha256).read_bytes() == original
    assert "verdict" not in result.model_dump()
