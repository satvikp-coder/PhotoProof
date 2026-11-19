"""Actual detector records retain their measured sampling/validity and native scale."""

import io
from types import SimpleNamespace

import numpy as np
import pytest
from PIL import Image

from photoproof.contracts.arrays import encode_npy
from photoproof.contracts.exceptions import InputRejectedError
from photoproof.plugins.compression.ela.algorithm import analyze_ela, ela_result_to_dict
from photoproof.plugins.compression.jpeg_dct.algorithm import analyze_jpeg_dct
from photoproof.plugins.compression.jpeg_grid.algorithm import (
    analyze_jpeg_grid,
    grid_result_to_dict,
)
from photoproof.plugins.fusion.normalize.native import normalize_anomaly, read_native_anomaly


def _record(values, valid=None):
    return {
        "values": values,
        "valid_mask": np.ones(np.shape(values), dtype=bool) if valid is None else valid,
        "block": [8, 16],
        "stride": [4, 8],
        "scale": "robust_z",
        "notes": [],
    }


def _normalize(record, **kwargs):
    return normalize_anomaly(
        record,
        detector_id="compression.ela_anomaly",
        source_sha256="a" * 64,
        image_size=(112, 80),
        strength_class="weak",
        z0=3,
        k=1.5,
        tail=kwargs.pop("tail", "upper"),
        **kwargs,
    )


def test_native_z_is_monotonic_without_second_standardization():
    result = _normalize(_record([[0, 1, 3, 5, 1000]]))
    assert result is not None
    values = result.layer.map
    assert values[0, 0] == 0 and values[0, -1] == 1
    assert np.all(np.diff(values[0]) > 0)
    # Changing unrelated blocks cannot change this native z-score's mapping.
    other = _normalize(_record([[3, 1000]]))
    assert values[0, 2] == other.layer.map[0, 0]
    assert result.layer.calibration == "uncalibrated"
    assert result.sampling_grid.block_xy == (16, 8)
    assert result.sampling_grid.stride_xy == (8, 4)


def test_signed_noise_cues_need_explicit_two_sided_mapping():
    upper = _normalize(_record([[-4, 0, 4]]))
    both = _normalize(_record([[-4, 0, 4]]), tail="two_sided")
    assert upper.layer.map[0, 0] == 0
    assert both.layer.map[0, 0] == both.layer.map[0, 2] > 0
    assert both.layer.map[0, 1] == 0


def test_invalid_cells_are_not_measured_zero_anomalies():
    result = _normalize(_record([[np.nan, 0]], [[False, True]]))
    np.testing.assert_array_equal(result.layer.map, [[0, 0]])
    np.testing.assert_array_equal(result.layer.valid_mask, [[False, True]])
    assert _normalize(_record([[np.nan]], [[False]])) is None
    assert _normalize(_record([], [])) is None


def test_measured_constant_zero_map_is_available_without_a_fabricated_anomaly():
    result = _normalize(_record([[0, 0]]))
    assert result is not None and result.layer.valid_mask.all()
    assert not result.layer.map.any()


def test_sample_count_cannot_claim_cells_outside_the_source():
    with pytest.raises(InputRejectedError, match="outside"):
        _normalize(_record(np.zeros((1, 100))))


@pytest.mark.parametrize("z0,k", [(0, 1), (3, 1e-300), (1e308, 1e-300)])
def test_finite_extremes_saturate_without_nonfinite_scores(z0, k):
    result = normalize_anomaly(
        _record([[0, 1e308]]),
        detector_id="noise.consistency_map",
        source_sha256="a" * 64,
        image_size=(112, 80),
        strength_class="medium",
        z0=z0,
        k=k,
        tail="two_sided",
    )
    assert np.isfinite(result.layer.map).all()
    assert result.layer.map[0, 0] == 0


@pytest.mark.parametrize(
    "field,value",
    [
        ("values", [[np.nan]]),
        ("valid_mask", [[1]]),
        ("scale", "sigma"),
        ("block", [0, 8]),
        ("stride", [True, 8]),
        ("notes", ["x" * 257]),
    ],
)
def test_invalid_native_records_fail_visibly(field, value):
    record = _record([[0]])
    record[field] = value
    with pytest.raises(InputRejectedError):
        _normalize(record)


@pytest.mark.parametrize("name", ["ela", "dq", "grid"])
def test_real_compression_algorithm_records_are_consumed(name):
    pixels = np.random.default_rng(32).integers(0, 256, (80, 112, 3), dtype=np.uint8)
    if name == "ela":
        record = ela_result_to_dict(analyze_ela(pixels))["anomaly"]
    elif name == "grid":
        record = grid_result_to_dict(analyze_jpeg_grid(pixels))["anomaly"]
    else:
        buffer = io.BytesIO()
        Image.fromarray(pixels).save(buffer, format="JPEG", quality=85)
        _, dq = analyze_jpeg_dct(buffer.getvalue())
        record = dq["anomaly"]
    result = _normalize(record)
    assert result is not None
    assert result.layer.map.shape == np.shape(record["values"])
    np.testing.assert_array_equal(result.layer.valid_mask, record["valid_mask"])
    assert np.isfinite(result.layer.map).all()
    assert result.source_sha256 == "a" * 64
    contract = {
        "ela": "compression.ela_anomaly",
        "dq": "compression.dq_map",
        "grid": "compression.grid_map",
    }[name]
    ctx = SimpleNamespace(inputs={contract: {"anomaly": record} if name == "dq" else record})
    actual = read_native_anomaly(
        ctx,
        contract,
        geometry={"decoded_size": [112, 80], "source_sha256": "a" * 64},
        strength_class="weak",
        z0=3,
        k=1.5,
    )
    np.testing.assert_array_equal(actual.layer.map, result.layer.map)
    assert actual.layer.detector_id == contract


def test_noise_reads_declared_binary_scores_with_explicit_companion_metadata():
    calls = []
    record = _record([[999, 999, 999]])  # Legacy duplicated values are not authoritative.

    def artifact(name):
        calls.append(name)
        return encode_npy(np.array([[-4, 0, 4]], dtype=np.float32))

    ctx = SimpleNamespace(
        inputs={
            "noise.consistency_map": {"artifact": "declared reference"},
            "noise.variance_summary": {"consistency_map": record, "confidence": "high"},
        },
        artifact=artifact,
    )
    result = read_native_anomaly(
        ctx,
        "noise.consistency_map",
        geometry={"decoded_size": [112, 80], "source_sha256": "a" * 64},
        strength_class="medium",
        z0=3,
        k=1.5,
    )
    assert calls == ["noise.consistency_map"]
    assert result.layer.map[0, 0] == result.layer.map[0, 2] > 0
    assert result.layer.map[0, 1] == 0
    assert result.layer.calibration == "uncalibrated"
    ctx.inputs["noise.variance_summary"]["confidence"] = "low"
    low = read_native_anomaly(
        ctx,
        "noise.consistency_map",
        geometry={"decoded_size": [112, 80], "source_sha256": "a" * 64},
        strength_class="medium",
        z0=3,
        k=1.5,
    )
    assert low.layer.strength_class == "weak" and "low_confidence" in low.layer.notes
    np.testing.assert_array_equal(low.layer.map, result.layer.map)


def test_noise_without_sampling_metadata_is_unavailable_without_a_guessed_grid():
    def forbidden(name):
        pytest.fail("missing companion metadata must not trigger a speculative artifact read")

    ctx = SimpleNamespace(inputs={"noise.consistency_map": {}}, artifact=forbidden)
    assert (
        read_native_anomaly(
            ctx,
            "noise.consistency_map",
            geometry={"decoded_size": [112, 80], "source_sha256": "a" * 64},
            strength_class="medium",
            z0=3,
            k=1.5,
        )
        is None
    )


def test_raw_sigma_is_not_misrepresented_as_a_regional_anomaly():
    with pytest.raises(InputRejectedError, match="no native regional"):
        read_native_anomaly(
            SimpleNamespace(inputs={"noise.sigma_map": [[8]]}),
            "noise.sigma_map",
            geometry={"decoded_size": [112, 80], "source_sha256": "a" * 64},
            strength_class="medium",
            z0=3,
            k=1.5,
        )
