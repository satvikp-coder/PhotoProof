"""Native binary producer preserves actual host facts and avoids diagnostic pseudo-evidence."""

import logging
from types import SimpleNamespace

import numpy as np
import pytest

from photoproof.contracts.arrays import decode_npy
from photoproof.contracts.exceptions import InputRejectedError
from photoproof.plugins.compression.ela.algorithm import analyze_ela, ela_result_to_dict
from photoproof.plugins.fusion.normalize.native_processor import normalize_native
from tests.unit.test_score_grid_projection import _geometry


def _context(record=None):
    inputs = {
        "image.geometry": _geometry(112, 80),
        "pipeline.upstream_status": {
            "format_version": 1,
            "outputs": {
                "compression.ela_anomaly": {
                    "plugin_id": "compression.ela",
                    "state": "SUCCESS",
                    "failure_category": None,
                },
                "compression.dq_map": {
                    "plugin_id": "compression.jpeg_dct",
                    "state": "NOT_APPLICABLE",
                    "failure_category": None,
                },
                "compression.grid_map": {
                    "plugin_id": "compression.jpeg_grid",
                    "state": "FAILED",
                    "failure_category": "exception",
                },
                "noise.consistency_map": {
                    "plugin_id": "noise.variance_map",
                    "state": "PENDING",
                    "failure_category": None,
                },
            },
        },
        "compression.jpeg_tables": {"diagnostic": "does not authorize localized evidence"},
        "noise.sigma_global": 123.0,
    }
    if record is None:
        pixels = np.random.default_rng(32).integers(0, 256, (80, 112, 3), np.uint8)
        record = ela_result_to_dict(analyze_ela(pixels))["anomaly"]
    inputs["compression.ela_anomaly"] = record
    stored = {}

    def write(name, data, media_type):
        assert media_type == "application/x-npy"
        stored[name] = data
        return {"unit_test_ref": name}

    return SimpleNamespace(
        inputs=inputs, config=None, write_artifact=write, logger=logging.getLogger(__name__)
    ), stored


def test_actual_ela_record_becomes_binary_layer_with_unchanged_native_sampling():
    ctx, stored = _context()
    result = normalize_native(ctx)
    assert len(result["fusion.layers"]) == 1
    layer = result["fusion.layers"][0]
    assert layer["detector_id"] == "compression.ela_anomaly"
    assert layer["format_version"] == 2 and layer["calibration"] == "uncalibrated"
    values = decode_npy(stored[layer["map"]["unit_test_ref"]])
    valid = decode_npy(stored[layer["valid_mask"]["unit_test_ref"]])
    assert values.shape == valid.shape == np.shape(ctx.inputs["compression.ela_anomaly"]["values"])
    assert layer["sampling_grid"]["image_size"] == [112, 80]
    assert layer["strength_class"] == "weak"
    assert np.all((values >= 0) & (values <= 1))
    facts = result["fusion.availability"]["outputs"]
    assert facts["compression.dq_map"]["state"] == "NOT_APPLICABLE"
    assert facts["compression.grid_map"]["failure_category"] == "exception"
    assert facts["noise.consistency_map"]["state"] == "PENDING"
    assert facts["copymove.mask"]["state"] == "NOT_PLANNED"
    assert all(not facts[name]["available"] for name in result["fusion.unavailable"])
    assert set(result["fusion.availability"]["context_only"]) == {
        "compression.jpeg_tables",
        "noise.sigma_global",
    }


def test_measured_zero_stays_distinct_from_unavailable():
    record = {
        "values": [[0.0, 0.0, 0.0]],
        "valid_mask": [[True, True, False]],
        "block": [32, 32],
        "stride": [32, 32],
        "scale": "robust_z",
        "notes": [],
    }
    ctx, stored = _context(record)
    result = normalize_native(ctx)
    layer = result["fusion.layers"][0]
    scores = decode_npy(stored[layer["map"]["unit_test_ref"]])
    valid = decode_npy(stored[layer["valid_mask"]["unit_test_ref"]])
    assert not scores.any()
    np.testing.assert_array_equal(valid, [[True, True, False]])
    assert "compression.ela_anomaly" not in result["fusion.unavailable"]


def test_geometry_unavailable_has_no_layers_or_artifact_writes():
    ctx, stored = _context()
    del ctx.inputs["image.geometry"]
    result = normalize_native(ctx)
    assert result["fusion.layers"] == [] and not stored
    assert result["fusion.availability"]["source_sha256"] is None
    assert all(
        f["reason"] == "geometry_unavailable"
        for f in result["fusion.availability"]["outputs"].values()
    )


def test_failed_or_pending_producer_cannot_supply_usable_data():
    ctx, _ = _context()
    ctx.inputs["pipeline.upstream_status"]["outputs"]["compression.ela_anomaly"]["state"] = "FAILED"
    with pytest.raises(InputRejectedError, match="contradicts"):
        normalize_native(ctx)


def test_missing_output_has_no_zero_layer():
    ctx, stored = _context()
    del ctx.inputs["compression.ela_anomaly"]
    result = normalize_native(ctx)
    assert result["fusion.layers"] == [] and not stored
    assert (
        result["fusion.availability"]["outputs"]["compression.ela_anomaly"]["reason"]
        == "missing_output"
    )


def test_invalid_execution_snapshot_is_rejected():
    ctx, _ = _context()
    ctx.inputs["pipeline.upstream_status"]["format_version"] = True
    with pytest.raises(InputRejectedError, match="snapshot"):
        normalize_native(ctx)
