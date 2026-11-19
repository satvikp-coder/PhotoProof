"""Actual decoded pixels -> SIFT -> matches -> clusters -> traceable RANSAC inliers."""

import json

import numpy as np
import pytest
from PIL import Image

from photoproof.contracts.analysis import AnalysisRequest
from photoproof.contracts.copymove import (
    Clusters,
    Keypoints,
    Matches,
    VerificationBindings,
    Verified,
)
from photoproof.contracts.exceptions import InputRejectedError
from photoproof.core.controller import AnalysisController


@pytest.mark.parametrize(
    "kind,rotation,scale", [("translation", 0, 1), ("rotation", -30, 1), ("scale", 0, 0.8)]
)
def test_actual_verified_parameters_and_inlier_rows(make_app, tmp_path, kind, rotation, scale):
    side = 96 if kind == "translation" else 128
    patch = np.random.default_rng(22).integers(0, 256, (side, side, 3), np.uint8)
    copied = Image.fromarray(patch)
    if kind == "rotation":
        copied = copied.rotate(30, resample=Image.Resampling.BICUBIC, expand=True)
    elif kind == "scale":
        copied = copied.resize((102, 102), Image.Resampling.BICUBIC)
    pixels = np.full((512, 512, 3), 120, dtype=np.uint8)
    pixels[32 : 32 + side, 32 : 32 + side] = patch
    pixels[240 : 240 + copied.height, 240 : 240 + copied.width] = np.asarray(copied)
    path = tmp_path / "generated-copy.png"
    Image.fromarray(pixels).save(path)
    original = path.read_bytes()
    app = make_app("photoproof.plugins")
    target = app.ingestion().ingest_path(path, role="target")[0]
    result = AnalysisController(app).analyze(AnalysisRequest(target=target, use_cache=False))
    states = {p.plugin_id: p.state for p in result.plugins}
    assert states["copymove.verification"] == "SUCCESS", result.plugins

    def payload(name):
        ref = result.outputs[name]["artifact"]
        return app.storage.blob_path("artifacts", ref["sha256"]).read_bytes()

    features = Keypoints.from_npz(payload("copymove.keypoints"), source_sha256=target.sha256)
    matches = Matches.from_npz(payload("copymove.matches"), source_sha256=target.sha256)
    candidates = Clusters.from_json(
        payload("copymove.clusters").decode(), source_sha256=target.sha256
    )
    verified = Verified.from_npz(payload("copymove.verified"), source_sha256=target.sha256)
    inliers = Clusters.from_json(
        payload("copymove.verified_inliers").decode(), source_sha256=target.sha256
    )
    bindings = VerificationBindings.from_json(
        payload("copymove.verification_bindings").decode(), source_sha256=target.sha256
    )
    for binding_kind, name in [
        ("keypoints", "copymove.keypoints"),
        ("matches", "copymove.matches"),
        ("clusters", "copymove.clusters"),
        ("verified", "copymove.verified"),
        ("inliers", "copymove.verified_inliers"),
    ]:
        bindings.verify_payload(binding_kind, payload(name))
    with pytest.raises(InputRejectedError, match="bytes differ"):
        bindings.verify_payload("matches", payload("copymove.matches") + b"changed")
    assert any(
        abs(t.rotation_deg - rotation) <= 2 and abs(t.scale / scale - 1) <= 0.03
        for t in verified.transforms
    ), verified
    assert verified.image_size == inliers.image_size == (512, 512)
    assert inliers.num_matches == len(matches.pairs)
    original_groups = {c.id: c for c in candidates.clusters}
    selected_groups = {c.id: c for c in inliers.clusters}
    for transform in verified.transforms:
        group = selected_groups[transform.cluster_id]
        assert len(group.pair_indices) == transform.inliers >= 6
        assert set(group.pair_indices) <= set(original_groups[group.id].pair_indices)
        pairs = matches.pairs[np.array(group.pair_indices)]
        a, b = features.xy[pairs[:, 0]], features.xy[pairs[:, 1]]
        flip = (a[:, 0] > b[:, 0]) | ((a[:, 0] == b[:, 0]) & (a[:, 1] > b[:, 1]))
        source, target_points = np.where(flip[:, None], b, a), np.where(flip[:, None], a, b)
        residual = source @ transform.matrix[:, :2].T + transform.matrix[:, 2] - target_points
        assert np.all(np.linalg.norm(residual, axis=1) <= 3 + 1e-5)
    details = json.loads(payload("copymove.verification_details"))
    assert details["source_sha256"] == target.sha256 and details["format_version"] == 1
    assert details["calibration"] == verified.calibration == "uncalibrated"
    assert (
        details["bindings_sha256"]
        == result.outputs["copymove.verification_bindings"]["artifact"]["sha256"]
    )
    assert '"probability"' not in json.dumps(details)
    assert "verdict" not in result.outputs["copymove.verification_stats"]
    repeated = AnalysisController(app).analyze(AnalysisRequest(target=target, use_cache=False))
    for name in ("copymove.verified", "copymove.verified_inliers", "copymove.verification_details"):
        assert (
            repeated.outputs[name]["artifact"]["sha256"]
            == result.outputs[name]["artifact"]["sha256"]
        )
    assert app.storage.blob_path("objects", target.sha256).read_bytes() == original


def test_actual_flat_image_is_empty_verification_with_no_authenticity_claim(make_app, tmp_path):
    path = tmp_path / "flat.png"
    Image.fromarray(np.full((64, 64, 3), 120, np.uint8)).save(path)
    app = make_app("photoproof.plugins")
    target = app.ingestion().ingest_path(path, role="target")[0]
    result = AnalysisController(app).analyze(AnalysisRequest(target=target, use_cache=False))
    assert {p.plugin_id: p.state for p in result.plugins}["copymove.verification"] == "SUCCESS"
    stats = result.outputs["copymove.verification_stats"]
    assert stats["num_verified_clusters"] == 0 and stats["max_heuristic_score"] is None
    assert "verdict" not in stats
