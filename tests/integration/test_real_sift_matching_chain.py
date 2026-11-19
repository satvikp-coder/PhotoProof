"""Actual image decoding, SIFT artifacts and candidate matching through the controller."""

import json

import numpy as np
from PIL import Image

from photoproof.contracts.analysis import AnalysisRequest
from photoproof.contracts.arrays import decode_npz_numeric
from photoproof.core.controller import AnalysisController
from photoproof.plugins.copymove.matching.algorithm import analyze_sift_matching


def test_real_sift_artifact_drives_configured_matching(make_app, app_root, tmp_path):
    config = app_root / "configs" / "plugins" / "copymove.matching.yaml"
    config.write_text(
        "k: 5\nratio: 0.7\nmin_spatial_distance: 12\nmax_pairs: 1000\n", encoding="utf-8"
    )
    rng = np.random.default_rng(21)
    pixels = np.full((384, 512, 3), 120, dtype=np.uint8)
    patch = rng.integers(0, 256, (96, 96, 3), dtype=np.uint8)
    pixels[32:128, 32:128] = patch
    pixels[200:296, 200:296] = patch
    pixels[200:296, 350:446] = patch
    path = tmp_path / "synthetic-copies.png"
    Image.fromarray(pixels).save(path)
    original = path.read_bytes()
    app = make_app("photoproof.plugins")
    target = app.ingestion().ingest_path(path, role="target")[0]
    result = AnalysisController(app).analyze(AnalysisRequest(target=target, use_cache=False))
    states = {p.plugin_id: p.state for p in result.plugins}
    assert states["copymove.sift_keypoints"] == "SUCCESS", result.plugins
    assert states["copymove.matching"] == "SUCCESS", result.plugins
    summary = result.outputs["copymove.match_stats"]
    assert summary["config"]["k"] == 5
    assert summary["config"]["ratio"] == 0.7
    assert summary["config"]["min_spatial_distance"] == 12
    assert len(json.dumps(summary)) < 4096
    assert len(json.dumps(result.outputs["copymove.keypoint_stats"])) < 4096
    refs = result.outputs
    assert refs["copymove.keypoints"]["artifact"]["media_type"] == "application/x-npz"
    assert refs["copymove.matches"]["artifact"]["media_type"] == "application/x-npz"

    def read(name, names):
        ref = refs[name]["artifact"]
        return decode_npz_numeric(
            app.storage.blob_path("artifacts", ref["sha256"]).read_bytes(), names=frozenset(names)
        )

    features = read("copymove.keypoints", {"keypoints", "descriptors"})
    matches = read("copymove.matches", {"pairs", "distances"})
    pairs = matches["pairs"]
    assert len(pairs) > 0
    assert np.all(pairs[:, 0] != pairs[:, 1])
    deltas = features["keypoints"][pairs[:, 1], :2] - features["keypoints"][pairs[:, 0], :2]
    assert np.all(np.linalg.norm(deltas, axis=1) >= 12)
    deltas = np.where((deltas[:, :1] < 0), -deltas, deltas)
    planted = np.array([[168, 168], [318, 168], [150, 0]])
    close = np.max(np.abs(deltas[:, None, :] - planted[None, :, :]), axis=2) <= 2
    assert float(close.any(axis=1).mean()) >= 0.8
    assert close[:, 0].any() and close[:, 1].any()  # Both pasted copies are recovered.
    repeated = analyze_sift_matching(
        features["keypoints"],
        features["descriptors"],
        k=5,
        ratio=0.7,
        min_spatial_distance=12,
        max_pairs=1000,
    )
    np.testing.assert_array_equal(repeated["pairs"], pairs)
    np.testing.assert_array_equal(repeated["distances"], matches["distances"])
    assert len({tuple(sorted(pair)) for pair in pairs.tolist()}) == len(pairs)
    assert app.storage.blob_path("objects", target.sha256).read_bytes() == original


def test_no_keypoints_is_successful_empty_matching_not_a_forgery_verdict(make_app, tmp_path):
    path = tmp_path / "flat-fixture.png"
    Image.fromarray(np.full((64, 64, 3), 120, dtype=np.uint8)).save(path)
    app = make_app("photoproof.plugins")
    target = app.ingestion().ingest_path(path, role="target")[0]
    result = AnalysisController(app).analyze(AnalysisRequest(target=target, use_cache=False))
    states = {p.plugin_id: p.state for p in result.plugins}
    assert states["copymove.sift_keypoints"] == states["copymove.matching"] == "SUCCESS"
    assert result.outputs["copymove.match_stats"]["num_matches"] == 0
    assert "verdict" not in result.outputs["copymove.match_stats"]
