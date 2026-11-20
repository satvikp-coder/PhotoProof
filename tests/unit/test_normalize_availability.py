"""Unavailable native contracts never become clean maps or unverified strong evidence."""

import numpy as np
import pytest
from pydantic import ValidationError

from photoproof.contracts.arrays import decode_npy
from photoproof.contracts.exceptions import InputRejectedError
from photoproof.plugins.fusion.normalize import CONTEXT_OUTPUTS, DETECTOR_NORMALIZATION
from photoproof.plugins.fusion.normalize.processor import run
from photoproof.plugins.fusion.normalize.schema import Config
from tests.unit.test_normalize import _make_ctx, _record


def _run(value, *, config=None):
    ctx, stored = _make_ctx({"compression.ela_anomaly": value})
    if config is not None:
        from dataclasses import replace

        ctx = replace(ctx, config=config)
    return run(ctx), stored


@pytest.mark.parametrize("value", [None, {}, {"artifact": {"sha256": "a" * 64}}, "failed", []])
def test_unusable_output_has_no_layer(value):
    result, stored = _run(value)
    assert not result["fusion.layers"] and not stored
    assert "compression.ela_anomaly" in result["fusion.unavailable"]
    assert not result["fusion.availability"]["outputs"]["compression.ela_anomaly"]["available"]


def test_serialized_numeric_map_with_explicit_sampling_retains_usable_evidence():
    result, stored = _run(_record(np.array([[0.0, 1.0], [2.0, 4.0]], np.float32)))
    assert len(result["fusion.layers"]) == 1
    layer = result["fusion.layers"][0]
    assert decode_npy(stored[layer["valid_mask"].sha256]).all()
    assert decode_npy(stored[layer["map"].sha256]).max() > 0


def test_bare_numeric_grid_without_sampling_is_not_guessed_into_a_pixel_map():
    result, stored = _run([[0.0, 1.0], [2.0, 4.0]])
    assert not result["fusion.layers"] and not stored
    assert "compression.ela_anomaly" in result["fusion.unavailable"]


def test_unverified_binary_mask_without_companions_is_unavailable():
    ctx, stored = _make_ctx({"copymove.mask": [[0.0, 0.0], [0.0, 1.0]]})
    result = run(ctx)
    assert result["fusion.layers"] == [] and not stored
    assert "copymove.mask" in result["fusion.unavailable"]


@pytest.mark.parametrize("value", [float("nan"), [[0.0, float("inf")]]])
def test_nonfinite_native_evidence_fails_visibly(value):
    record = _record(np.atleast_2d(np.asarray(value, dtype=np.float32)))
    with pytest.raises(InputRejectedError, match="nonfinite"):
        _run(record)


def test_configured_mapping_is_applied():
    record = _record(np.array([[0.0, 1.0], [2.0, 4.0]], np.float32))
    default, default_data = _run(record)
    configured, configured_data = _run(
        record,
        config=Config.model_validate(
            {"mappings": {"compression.ela_anomaly": {"z0": 10.0, "k": 1.5}}}
        ),
    )
    scores = decode_npy(default_data[default["fusion.layers"][0]["map"].sha256])
    changed = decode_npy(configured_data[configured["fusion.layers"][0]["map"].sha256])
    assert changed[1, 1] < scores[1, 1]
    assert changed[0, 0] == scores[0, 0] == 0


@pytest.mark.parametrize(
    "mapping",
    [
        {"unknown": {}},
        {"compression.ela_anomaly": {"k": 0}},
        {"compression.ela_anomaly": {"method": "identity"}},
    ],
)
def test_invalid_mapping_rejected(mapping):
    with pytest.raises(ValidationError):
        Config.model_validate({"mappings": mapping})


def test_raw_noise_measurements_are_context_not_inconsistency_evidence():
    for name in [
        "noise.residual",
        "noise.sigma_global",
        "noise.residual_summary",
        "noise.variance_summary",
    ]:
        assert name in CONTEXT_OUTPUTS and name not in DETECTOR_NORMALIZATION
        ctx, stored = _make_ctx({name: 123.0})
        result = run(ctx)
        assert result["fusion.layers"] == [] and not stored
        assert name in result["fusion.availability"]["context_only"]


def test_configuration_cannot_promote_a_weak_detector_to_strong():
    with pytest.raises(ValidationError, match="cannot promote"):
        Config.model_validate(
            {"mappings": {"compression.ela_anomaly": {"strength_class": "strong"}}}
        )


def test_configuration_can_lower_a_verified_documented_class():
    from tests.unit.test_native_copy_move_normalization import _transport

    ctx, _, _ = _transport()
    ctx.inputs["pipeline.upstream_status"] = {
        "format_version": 1,
        "required_prior_outputs": [],
        "outputs": {
            "copymove.mask": {
                "plugin_id": "copymove.mask",
                "state": "SUCCESS",
                "failure_category": None,
            }
        },
    }
    ctx.config = Config.model_validate({"mappings": {"copymove.mask": {"strength_class": "weak"}}})
    assert run(ctx)["fusion.layers"][0]["strength_class"] == "weak"
