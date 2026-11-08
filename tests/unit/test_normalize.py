"""Tests for fusion.normalize (#32): optional_inputs and EvidenceLayer normalization.

Covers:
- Plugin with optional_inputs runs even when none are present (absent inputs never
  cause SKIP).
- Present optional inputs are included in PluginContext.inputs; absent ones are not.
- EvidenceLayer normalization maps map-type outputs to [0, 1] and scalar outputs to
  single-cell maps.
- All layers carry calibration: uncalibrated.
- Detector mapping parameters and strength class are documented.
- Monotonic normalization: higher input z-score => higher normalized score.
"""

from __future__ import annotations

import numpy as np
import pytest

from photoproof.contracts.evidence import EvidenceLayer
from photoproof.contracts.exceptions import PolicyError
from photoproof.contracts.plugins import PluginContext
from photoproof.plugins.fusion.normalize import (
    DETECTOR_NORMALIZATION,
    METADATA,
    UNCALIBRATED,
)
from photoproof.plugins.fusion.normalize.processor import (
    _logistic_squash,
    _normalize_map,
    run,
)

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
    """METADATA declares api_version 1.2.0 and optional_inputs."""
    assert METADATA["api_version"] == "1.2.0"
    assert isinstance(METADATA["optional_inputs"], tuple)
    assert len(METADATA["optional_inputs"]) > 0
    # All declared optional_inputs have normalization config.
    for name in METADATA["optional_inputs"]:
        assert name in DETECTOR_NORMALIZATION, f"{name} missing normalization config"


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


def _make_ctx(inputs: dict[str, object]) -> PluginContext:
    return PluginContext(
        run_id="run-1",
        plugin_id="fusion.normalize",
        inputs=inputs,
        config={},
        seed=0,
        write_artifact=lambda name, data, media_type: None,
        logger=None,  # type: ignore[arg-type]
    )


def test_processor_runs_with_no_optional_inputs() -> None:
    """fusion.normalize runs even when no detector outputs are present."""
    result = run(_make_ctx({}))
    assert "fusion.layers" in result
    assert result["fusion.layers"] == []
    assert set(result["fusion.unavailable"]) == set(DETECTOR_NORMALIZATION.keys())


def test_processor_includes_present_optional_inputs() -> None:
    """Present optional inputs appear in ctx.inputs; absent ones do not."""
    ela_map = np.random.default_rng(0).normal(0, 1, (8, 8)).astype(np.float32)
    result = run(_make_ctx({"compression.ela_map": ela_map}))
    assert len(result["fusion.layers"]) == 1
    layer = result["fusion.layers"][0]
    assert layer["detector_id"] == "compression.ela_map"
    assert layer["calibration"] == "uncalibrated"
    assert layer["strength_class"] == "weak"
    # All map values in [0, 1].
    arr = np.array(layer["map"], dtype=np.float32)
    assert np.all((arr >= 0) & (arr <= 1))
    # Absent detectors are unavailable.
    unavailable = set(result["fusion.unavailable"])
    assert "compression.ela_map" not in unavailable
    assert "noise.residual" in unavailable


def test_processor_scalar_output_becomes_single_cell_map() -> None:
    """Scalar summary output becomes a single-cell map."""
    result = run(_make_ctx({"compression.ela_summary": 3.5}))
    assert len(result["fusion.layers"]) == 1
    layer = result["fusion.layers"][0]
    arr = np.array(layer["map"], dtype=np.float32)
    assert arr.shape == (1, 1)
    assert 0.0 < arr[0, 0] < 1.0


def test_processor_map_values_in_unit_interval() -> None:
    """Every normalized map value is in [0, 1]."""
    rng = np.random.default_rng(42)
    for name in DETECTOR_NORMALIZATION:
        if name not in ("compression.jpeg_tables", "copymove.verification"):
            # Use a map-type output.
            values = rng.normal(0, 1, (6, 6)).astype(np.float32)
            result = run(_make_ctx({name: values}))
            assert len(result["fusion.layers"]) == 1
            arr = np.array(result["fusion.layers"][0]["map"], dtype=np.float32)
            assert np.all((arr >= 0) & (arr <= 1))


# -------------------------------------------------------------- PluginContext image()


def test_plugin_context_refuses_undeclared_input() -> None:
    """PluginContext.image() refuses inputs not declared (required or optional)."""
    ctx = _make_ctx({"compression.ela_map": np.zeros((2, 2), dtype=np.float32)})
    # fusion.normalize declares compression.ela_map as optional_input -> present.
    arr = ctx.image("compression.ela_map")
    assert isinstance(arr, np.ndarray)
    # A detector not declared at all is refused.
    with pytest.raises(PolicyError, match="did not declare input"):
        ctx.image("noise.residual")


def test_plugin_context_read_only_array() -> None:
    """Arrays handed to plugins are read-only."""
    arr = np.ones((2, 2), dtype=np.float32)
    ctx = _make_ctx({"compression.ela_map": arr})
    out = ctx.image("compression.ela_map")
    assert not out.flags.writeable
    with pytest.raises(ValueError):
        out[0, 0] = 99.0


# ------------------------------------------------------------------ monotonic normalization


def test_normalization_is_monotonic_per_detector() -> None:
    """Higher input values produce higher normalized scores (monotonic mapping)."""
    rng = np.random.default_rng(7)
    base = rng.normal(0, 1, (8, 8)).astype(np.float32)
    base[3, 3] = 50.0  # strong outlier
    result = run(_make_ctx({"compression.ela_map": base}))
    assert len(result["fusion.layers"]) == 1
    layer = result["fusion.layers"][0]
    arr = np.array(layer["map"], dtype=np.float32)
    # The outlier cell should have the highest score.
    max_idx = np.unravel_index(np.argmax(arr), arr.shape)
    assert max_idx == (3, 3)
