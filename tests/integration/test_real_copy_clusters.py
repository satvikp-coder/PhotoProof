"""Actual isolated decoding, SIFT, matching and typed candidate clustering."""

import numpy as np
import pytest
from PIL import Image

from photoproof.contracts.analysis import AnalysisRequest
from photoproof.contracts.copymove import Clusters
from photoproof.core.controller import AnalysisController


def _iou(a, b):
    intersection = max(0, min(a[2], b[2]) - max(a[0], b[0])) * max(
        0, min(a[3], b[3]) - max(a[1], b[1])
    )
    area_a = (a[2] - a[0]) * (a[3] - a[1])
    area_b = (b[2] - b[0]) * (b[3] - b[1])
    return intersection / (area_a + area_b - intersection)


@pytest.mark.parametrize("transform", ["translation", "rotation", "scale"])
def test_actual_candidate_clusters_recover_planted_region_boxes(make_app, tmp_path, transform):
    pixels = np.full((512, 512, 3), 120, dtype=np.uint8)
    side = 96 if transform == "translation" else 128
    patch = np.random.default_rng(22).integers(0, 256, (side, side, 3), np.uint8)
    copied = Image.fromarray(patch)
    if transform == "rotation":
        copied = copied.rotate(30, resample=Image.Resampling.BICUBIC, expand=True)
    elif transform == "scale":
        copied = copied.resize((102, 102), Image.Resampling.BICUBIC)
    width, height = copied.size
    pixels[32 : 32 + side, 32 : 32 + side] = patch
    pixels[240 : 240 + height, 240 : 240 + width] = np.asarray(copied)
    source_box = (32, 32, 32 + side, 32 + side)
    target_box = (240, 240, 240 + height, 240 + width)
    path = tmp_path / "copy-regions.png"
    Image.fromarray(pixels).save(path)
    original = path.read_bytes()
    app = make_app("photoproof.plugins")
    target = app.ingestion().ingest_path(path, role="target")[0]
    result = AnalysisController(app).analyze(AnalysisRequest(target=target, use_cache=False))
    states = {p.plugin_id: p.state for p in result.plugins}
    for name in ("copymove.sift_keypoints", "copymove.matching", "copymove.clustering"):
        assert states[name] == "SUCCESS", result.plugins
    ref = result.outputs["copymove.clusters"]["artifact"]
    clusters = Clusters.from_json(
        app.storage.blob_path("artifacts", ref["sha256"]).read_text(encoding="utf-8"),
        source_sha256=target.sha256,
    )
    assert clusters.image_size == (512, 512) and clusters.clusters
    assert any(
        _iou(c.source_bbox, source_box) >= 0.5 and _iou(c.target_bbox, target_box) >= 0.5
        for c in clusters.clusters
    ), clusters
    assert clusters.num_matches == result.outputs["copymove.match_stats"]["num_matches"]
    summary = result.outputs["copymove.cluster_stats"]
    assert summary["method_used"] == "hierarchical"
    assert summary["config"]["cut_threshold"] == 0.5
    assert summary["bbox_policy"] == "source_point_envelope_affine_target"
    assert summary["clusters"] == [
        {"id": c.id, "size": len(c.pair_indices)} for c in clusters.clusters
    ]
    repeated = AnalysisController(app).analyze(AnalysisRequest(target=target, use_cache=False))
    assert repeated.outputs["copymove.clusters"]["artifact"]["sha256"] == ref["sha256"]
    assert app.storage.blob_path("objects", target.sha256).read_bytes() == original
    assert "verdict" not in summary and "score" not in summary


def test_empty_actual_matches_produce_empty_candidate_clusters(make_app, tmp_path):
    path = tmp_path / "flat.png"
    Image.fromarray(np.full((64, 64, 3), 120, dtype=np.uint8)).save(path)
    app = make_app("photoproof.plugins")
    target = app.ingestion().ingest_path(path, role="target")[0]
    result = AnalysisController(app).analyze(AnalysisRequest(target=target, use_cache=False))
    states = {p.plugin_id: p.state for p in result.plugins}
    assert states["copymove.clustering"] == "SUCCESS"
    assert result.outputs["copymove.cluster_stats"]["num_clusters"] == 0


def test_two_actual_independent_copies_remain_two_clusters(make_app, tmp_path):
    pixels = np.full((512, 768, 3), 120, dtype=np.uint8)
    for seed, source, target in [(22, (32, 32), (240, 240)), (23, (32, 450), (350, 620))]:
        patch = np.random.default_rng(seed).integers(0, 256, (96, 96, 3), np.uint8)
        for y, x in (source, target):
            pixels[y : y + 96, x : x + 96] = patch
    path = tmp_path / "independent-copies.png"
    Image.fromarray(pixels).save(path)
    app = make_app("photoproof.plugins")
    target = app.ingestion().ingest_path(path, role="target")[0]
    result = AnalysisController(app).analyze(AnalysisRequest(target=target, use_cache=False))
    assert {p.plugin_id: p.state for p in result.plugins}["copymove.clustering"] == "SUCCESS"
    ref = result.outputs["copymove.clusters"]["artifact"]
    clusters = Clusters.from_json(
        app.storage.blob_path("artifacts", ref["sha256"]).read_text(encoding="utf-8"),
        source_sha256=target.sha256,
    )
    assert len(clusters.clusters) == 2
    for source, target_box in [
        ((32, 32, 128, 128), (240, 240, 336, 336)),
        ((32, 450, 128, 546), (350, 620, 446, 716)),
    ]:
        assert any(
            _iou(c.source_bbox, source) >= 0.5 and _iou(c.target_bbox, target_box) >= 0.5
            for c in clusters.clusters
        )
