"""Native normalization contracts, artifact scores, applicability, and context-only diagnostics.

Private numerical helpers retain regression controls; the production entry point
requires source/sampling metadata and actual host execution facts.
"""

from __future__ import annotations

import hashlib
import logging

import numpy as np
import pytest

from photoproof.contracts.arrays import decode_npy, encode_npy
from photoproof.contracts.artifacts import ArtifactRef
from photoproof.contracts.evidence import EvidenceLayer
from photoproof.contracts.exceptions import PolicyError
from photoproof.contracts.plugins import PluginContext
from photoproof.plugins.fusion.normalize import (
    DETECTOR_NORMALIZATION,
    METADATA,
    NATIVE_OUTPUTS,
    UNCALIBRATED,
)
from photoproof.plugins.fusion.normalize.processor import (
    _logistic_squash,
    _normalize_map,
    run,
)
from tests.unit.test_score_grid_projection import _geometry

# ------------------------------------------------------------------ contract checks


def test_evidence_layer_contract() -> None:
    """EvidenceLayer is defined, documented and versioned."""
    layer = EvidenceLayer(
        detector_id="compression.ela_map",
        map=np.zeros((4, 4), dtype=np.float32),
        regions=((0, 0, 2, 2),),
        strength_class="weak",
        valid_mask=np.ones((4, 4), dtype=bool),
        calibration="uncalibrated",
        notes=("test",),
    )
    assert layer.detector_id == "compression.ela_map"
    assert layer.strength_class == "weak"
    assert layer.calibration == "uncalibrated"
    assert layer.map.shape == (4, 4)
    assert layer.valid_mask.all()


def test_all_layers_are_uncalibrated() -> None:
    """Every documented detector carries calibration: uncalibrated until #37."""
    for _name, cfg in DETECTOR_NORMALIZATION.items():
        assert "strength_class" in cfg
        assert cfg["strength_class"] in {"weak", "medium", "strong"}
        assert "z0" in cfg and "k" in cfg
        assert cfg["z0"] > 0 and cfg["k"] > 0
    assert UNCALIBRATED == "uncalibrated"


def test_metadata_api_version_and_optional_inputs() -> None:
    """API1.9 declares native measurements and their provenance/status companions."""
    assert METADATA["api_version"] == "1.9.0"
    assert isinstance(METADATA["optional_inputs"], tuple)
    assert set(NATIVE_OUTPUTS) <= set(METADATA["optional_inputs"])
    assert set(NATIVE_OUTPUTS) == set(DETECTOR_NORMALIZATION)
    assert {"image.geometry", "pipeline.upstream_status", "copymove.verification_bindings"} <= set(
        METADATA["optional_inputs"]
    )


# ------------------------------------------------------------------ normalization mapping


def test_logistic_squash_is_monotonic() -> None:
    """Logistic squashing is strictly monotonic: higher z => higher score."""
    zs = np.array([-10.0, -1.0, 0.0, 1.0, 10.0], dtype=np.float32)
    scores = _logistic_squash(zs, z0=0.0, k=1.0)
    assert np.all(np.diff(scores) > 0)
    assert np.all(scores > 0) and np.all(scores < 1)


def test_normalize_map_is_monotonic() -> None:
    """Robust z-score + logistic squash is monotonic in input values."""
    values = np.linspace(-100, 100, 50, dtype=np.float32).reshape(5, 10)
    norm = _normalize_map(values, z0=0.0, k=1.0)
    # Check monotonicity along each row.
    for row in norm:
        assert np.all(np.diff(row) > 0)
    assert np.all(norm > 0) and np.all(norm < 1)


def test_normalize_map_constant_input_is_zeros() -> None:
    """Constant input (MAD=0) normalizes to zeros."""
    values = np.full((4, 4), 7.0, dtype=np.float32)
    norm = _normalize_map(values, z0=0.0, k=1.0)
    assert np.all(norm == 0.0)


def test_normalize_map_handles_non_finite() -> None:
    """Non-finite values are replaced with 0 before normalization."""
    values = np.array([[1.0, np.inf], [np.nan, -1.0]], dtype=np.float32)
    norm = _normalize_map(values, z0=0.0, k=1.0)
    assert np.all(np.isfinite(norm))
    assert np.all((norm >= 0) & (norm <= 1))


# ------------------------------------------------------------------ processor with optional inputs


def _make_ctx(inputs):
    values = dict(inputs)
    values.setdefault("image.geometry", _geometry(64, 64))
    values.setdefault(
        "pipeline.upstream_status",
        {
            "format_version": 1,
            "required_prior_outputs": [],
            "outputs": {
                name: {
                    "plugin_id": "control.producer",
                    "state": "SUCCESS",
                    "failure_category": None,
                }
                for name in values
                if name in NATIVE_OUTPUTS
            },
        },
    )
    stored = {}

    def writer(name, data, media_type):
        digest = hashlib.sha256(data).hexdigest()
        stored[digest] = data
        return ArtifactRef(
            name=name,
            sha256=digest,
            size=len(data),
            media_type=media_type,
            producer="fusion.normalize",
        )

    def reader(name, max_bytes, selector):
        node = values[name]
        for step in selector:
            node = node[step]
        ref = (
            node if isinstance(node, ArtifactRef) else ArtifactRef.model_validate(node["artifact"])
        )
        payload = stored[ref.sha256]
        assert len(payload) <= max_bytes and hashlib.sha256(payload).hexdigest() == ref.sha256
        return payload

    return PluginContext(
        run_id="run-1",
        plugin_id="fusion.normalize",
        inputs=values,
        config={},
        seed=0,
        write_artifact=writer,
        read_artifact=reader,
        logger=logging.getLogger(__name__),
    ), stored


def test_processor_runs_with_no_optional_inputs() -> None:
    """No detector measurements produces explicit unavailable entries, never zero layers."""
    ctx, _ = _make_ctx({})
    result = run(ctx)
    assert result["fusion.layers"] == []
    assert set(result["fusion.unavailable"]) == set(NATIVE_OUTPUTS)
    assert all(
        f["state"] == "NOT_PLANNED" for f in result["fusion.availability"]["outputs"].values()
    )


def test_processor_includes_present_optional_inputs() -> None:
    values = np.random.default_rng(0).normal(0, 1, (8, 8)).astype(np.float32)
    ctx, stored = _make_ctx({"compression.ela_anomaly": _record(values)})
    result = run(ctx)
    assert len(result["fusion.layers"]) == 1
    layer = result["fusion.layers"][0]
    assert layer["detector_id"] == "compression.ela_anomaly"
    assert layer["calibration"] == "uncalibrated" and layer["strength_class"] == "weak"
    arr = decode_npy(stored[layer["map"].sha256])
    assert np.all((arr >= 0) & (arr <= 1))
    assert "compression.ela_anomaly" not in result["fusion.unavailable"]
    assert "noise.consistency_map" in result["fusion.unavailable"]


def test_processor_scalar_output_is_context_not_localized_evidence() -> None:
    """A diagnostic scalar cannot authorize a one-cell forensic map or coverage."""
    ctx, stored = _make_ctx({"compression.ela_summary": 3.5})
    result = run(ctx)
    assert result["fusion.layers"] == [] and not stored
    assert "compression.ela_summary" in result["fusion.availability"]["context_only"]


def test_processor_map_values_in_unit_interval() -> None:
    """Actual native records, including binary noise companions, yield finite unit maps."""
    rng = np.random.default_rng(42)
    for name in NATIVE_OUTPUTS:
        if name == "copymove.mask":
            continue  # Verified mask provenance is covered by adapter/controller tests.
        values = rng.normal(0, 4, (6, 6)).astype(np.float32)
        record = _record(values)
        inputs = {name: {"anomaly": record} if name == "compression.dq_map" else record}
        ctx, stored = _make_ctx(inputs)
        if name == "noise.consistency_map":
            ref = ctx.write_artifact("noise-control.npy", encode_npy(values), "application/x-npy")
            ctx.inputs[name] = ref
            ctx.inputs["noise.variance_summary"] = {"consistency_map": record, "confidence": "high"}
        result = run(ctx)
        assert len(result["fusion.layers"]) == 1
        arr = decode_npy(stored[result["fusion.layers"][0]["map"].sha256])
        assert np.all(np.isfinite(arr)) and np.all((arr >= 0) & (arr <= 1))


# -------------------------------------------------------------- PluginContext image()


def test_plugin_context_refuses_undeclared_input() -> None:
    """PluginContext.image() refuses inputs not declared (required or optional)."""
    ctx, _ = _make_ctx({"compression.ela_map": np.zeros((2, 2), dtype=np.float32)})
    # fusion.normalize declares compression.ela_map as optional_input -> present.
    arr = ctx.image("compression.ela_map")
    assert isinstance(arr, np.ndarray)
    # A detector not declared at all is refused.
    with pytest.raises(PolicyError, match="did not declare input"):
        ctx.image("noise.residual")


def test_plugin_context_read_only_array() -> None:
    """Arrays handed to plugins are read-only."""
    arr = np.ones((2, 2), dtype=np.float32)
    ctx, _ = _make_ctx({"compression.ela_map": arr})
    out = ctx.image("compression.ela_map")
    assert not out.flags.writeable
    with pytest.raises(ValueError):
        out[0, 0] = 99.0


# ------------------------------------------------------------------ monotonic normalization


def test_normalization_is_monotonic_per_detector() -> None:
    values = np.random.default_rng(7).normal(0, 1, (8, 8)).astype(np.float32)
    values[3, 3] = 50.0
    ctx, stored = _make_ctx({"compression.ela_anomaly": _record(values)})
    result = run(ctx)
    arr = decode_npy(stored[result["fusion.layers"][0]["map"].sha256])
    assert np.unravel_index(np.argmax(arr), arr.shape) == (3, 3)


def _record(values):
    return {
        "values": values.tolist(),
        "valid_mask": np.ones(values.shape, bool).tolist(),
        "block": [8, 8],
        "stride": [8, 8],
        "scale": "robust_z",
        "notes": [],
    }
