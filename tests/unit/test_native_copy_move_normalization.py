"""Unit control: real RANSAC/mask algorithms and canonical artifacts, not accuracy data."""

import json
from types import SimpleNamespace

import numpy as np
import pytest

from photoproof.contracts.exceptions import InputRejectedError
from photoproof.plugins.copymove.mask.processor import run as mask_run
from photoproof.plugins.fusion.normalize.copymove import read_copy_move
from tests.unit.test_score_grid_projection import _geometry
from tests.unit.test_verified_dense_localization import fixture_models


def _transport(*, caution=False):
    from hashlib import sha256

    from photoproof.contracts.copymove import VerificationBindings

    fixture, features, matches, outcome = fixture_models(textured_only=True)
    if caution:
        from dataclasses import replace

        outcome = replace(
            outcome,
            verified=outcome.verified.model_copy(
                update={
                    "transforms": tuple(
                        replace(
                            t,
                            flags=(*t.flags, "possible_repetitive_structure"),
                            score=min(t.score, 0.25),
                        )
                        for t in outcome.verified.transforms
                    )
                }
            ),
        )
    candidates = outcome.inliers
    data = {
        "copymove.keypoints": features.to_npz(),
        "copymove.matches": matches.to_npz(),
        "copymove.clusters": candidates.to_json().encode(),
        "copymove.verified": outcome.verified.to_npz(),
        "copymove.verified_inliers": outcome.inliers.to_json().encode(),
    }
    bindings = VerificationBindings(
        source_sha256=features.source_sha256,
        image_size=features.image_size,
        keypoints_sha256=sha256(data["copymove.keypoints"]).hexdigest(),
        matches_sha256=sha256(data["copymove.matches"]).hexdigest(),
        clusters_sha256=sha256(data["copymove.clusters"]).hexdigest(),
        verified_sha256=sha256(data["copymove.verified"]).hexdigest(),
        inliers_sha256=sha256(data["copymove.verified_inliers"]).hexdigest(),
        geometric_model="similarity",
        reproj_threshold=3.0,
        min_inliers=6,
        seed=24,
        ransac_stop_confidence=0.99,
    )
    data["copymove.verification_bindings"] = bindings.to_json().encode()
    geometry = _geometry(640, 512)

    def reader(name, **kwargs):
        assert name in ctx.inputs
        assert len(data[name]) <= kwargs.get("max_bytes", 64 << 20)
        return data[name]

    stored = {}

    def writer(name, payload, media_type):
        stored[name] = payload
        return {"unit_test_ref": name}

    ctx = SimpleNamespace(
        inputs={name: {} for name in data},
        artifact=reader,
        config=None,
        image=lambda name: fixture.image,
        write_artifact=writer,
    )
    ctx.inputs["image.geometry"] = geometry
    outputs = mask_run(ctx)
    for name, ref in outputs.items():
        if isinstance(ref, dict) and "unit_test_ref" in ref:
            data[name] = stored[ref["unit_test_ref"]]
            ctx.inputs[name] = ref
    return ctx, geometry, data


def test_actual_dense_payloads_normalize_with_quality_and_validity():
    ctx, geometry, _ = _transport()
    result = read_copy_move(ctx, geometry=geometry)
    assert result is not None
    assert result.layer.strength_class == "strong"
    assert result.layer.calibration == "uncalibrated"
    assert result.sampling_grid.image_size == (640, 512)
    assert np.any(result.layer.map > 0.8)
    assert not result.layer.map[~result.layer.valid_mask].any()
    assert "localized_search_scope" in result.layer.notes


def test_missing_companion_does_not_create_zero_evidence():
    ctx, geometry, _ = _transport()
    del ctx.inputs["copymove.correlation_validity"]
    assert read_copy_move(ctx, geometry=geometry) is None


@pytest.mark.parametrize("fault", ["source", "frame", "calibration", "digest", "duplicate", "nan"])
def test_invalid_manifest_or_bound_bytes_fail(fault):
    ctx, geometry, data = _transport()
    manifest = json.loads(data["copymove.regions"])
    if fault == "source":
        manifest["source_sha256"] = "b" * 64
    elif fault == "frame":
        manifest["image_size"] = [641, 512]
    elif fault == "calibration":
        manifest["calibration"] = "calibrated:imaginary"
    elif fault == "digest":
        manifest["correlation_sha256"] = "b" * 64
    elif fault == "nan":
        manifest["unknown"] = float("nan")
    text = json.dumps(manifest)
    if fault == "duplicate":
        text = text[:-1] + ', "schema": "photoproof.copymove.dense-regions"}'
    data["copymove.regions"] = text.encode()
    with pytest.raises(InputRejectedError):
        read_copy_move(ctx, geometry=geometry)


def test_operator_can_only_weaken_the_documented_class():
    ctx, geometry, _ = _transport()
    result = read_copy_move(ctx, geometry=geometry, strength_class="weak")
    assert result.layer.strength_class == "weak"


def test_actual_upstream_reliability_caution_prevents_strong_evidence():
    ctx, geometry, _ = _transport(caution=True)
    result = read_copy_move(ctx, geometry=geometry)
    assert result.layer.strength_class == "weak"
    assert result.layer.map.max() <= 0.25
    assert "upstream_cautions_present" in result.layer.notes
