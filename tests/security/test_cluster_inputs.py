"""Semantic artifact failures and resource exhaustion never become empty success."""

import numpy as np
import pytest
from PIL import Image

from photoproof.contracts.analysis import AnalysisRequest
from photoproof.contracts.copymove import Matches
from photoproof.core.controller import AnalysisController


@pytest.mark.parametrize("fault", ["wrong_source", "pair_budget"])
def test_actual_bad_cluster_inputs_fail_without_publishing_hypotheses(
    make_app, app_root, tmp_path, monkeypatch, fault
):
    if fault == "wrong_source":
        writer = Matches.to_npz

        def wrong_source(self):
            return writer(self.model_copy(update={"source_sha256": "b" * 64}))

        monkeypatch.setattr(Matches, "to_npz", wrong_source)
    else:
        (app_root / "configs" / "plugins" / "copymove.clustering.yaml").write_text(
            "max_input_pairs: 1\n", encoding="utf-8"
        )
    pixels = np.full((512, 512, 3), 120, dtype=np.uint8)
    patch = np.random.default_rng(22).integers(0, 256, (96, 96, 3), np.uint8)
    pixels[32:128, 32:128] = patch
    pixels[240:336, 240:336] = patch
    path = tmp_path / "synthetic.png"
    Image.fromarray(pixels).save(path)
    original = path.read_bytes()
    app = make_app("photoproof.plugins")
    target = app.ingestion().ingest_path(path, role="target")[0]
    result = AnalysisController(app).analyze(AnalysisRequest(target=target, use_cache=False))
    plugins = {p.plugin_id: p for p in result.plugins}
    assert plugins["copymove.matching"].state == "SUCCESS"
    assert plugins["copymove.clustering"].state == "FAILED"
    assert "copymove.clusters" not in result.outputs
    assert "copymove.cluster_stats" not in result.outputs
    assert app.storage.blob_path("objects", target.sha256).read_bytes() == original
    assert "verdict" not in result.model_dump()
