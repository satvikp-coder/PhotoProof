"""Contract controls for actual artifact readers, family fusion, and attribution writers."""

import json
from types import SimpleNamespace

import numpy as np
import pytest

from photoproof.contracts.contributions import read_contributions
from photoproof.contracts.evidence import EvidenceLayer
from photoproof.contracts.evidence_io import SpatialEvidence, write_evidence_layer
from photoproof.contracts.exceptions import InputRejectedError
from photoproof.contracts.fused_maps import read_fused_scores
from photoproof.contracts.grids import SamplingGrid
from photoproof.plugins.fusion.rules.native_processor import run_native
from tests.unit.test_score_grid_projection import _geometry


def _context():
    ctx = SimpleNamespace(inputs={}, config=None)
    stored = {}

    def writer(name, data, media_type):
        assert media_type == "application/x-npy"
        stored[name] = data
        return {"unit_test_ref": name}

    def reader(name, *, selector=()):
        node = ctx.inputs[name]
        for step in selector:
            node = node[step]
        return stored[node["unit_test_ref"]]

    ctx.write_artifact, ctx.artifact = writer, reader
    geometry = _geometry(12, 8)
    entries = []
    for ordinal, name in enumerate(["compression.dq_map", "noise.consistency_map"]):
        evidence = SpatialEvidence(
            EvidenceLayer(
                name,
                np.full((2, 3), 0.6, np.float32),
                (),
                "medium",
                np.ones((2, 3), bool),
                "uncalibrated",
            ),
            "a" * 64,
            SamplingGrid((12, 8), (0, 0), (4, 4), (4, 4)),
        )
        entries.append(write_evidence_layer(ctx, evidence, ordinal=ordinal))
    ctx.inputs.update(
        {
            "image.geometry": geometry,
            "fusion.layers": entries,
            "fusion.unavailable": ["copymove.mask"],
        }
    )
    return ctx, geometry


def test_binary_input_to_binary_fused_map_and_exact_attribution():
    ctx, geometry = _context()
    result = run_native(ctx)
    ctx.inputs.update(result)
    fused = read_fused_scores(ctx, geometry=geometry)
    entries = read_contributions(ctx, geometry=geometry, fused=fused)
    assert fused.scores.shape == (2, 3) and fused.valid_mask.all()
    np.testing.assert_allclose(fused.scores, 1 - (1 - 0.6 * 0.6) ** 2, atol=1e-6)
    assert {e.family for e in entries} == {"compression", "noise"}
    assert all(e.scores.any() and e.measured.all() for e in entries)
    assert all(not e.scores.flags.writeable for e in entries)
    assert result["fusion.score_info"]["calibration"] == "uncalibrated"
    assert len(json.dumps(result)) < 4096


@pytest.mark.parametrize("fault", ["source", "promotion", "duplicate", "unverified_mask"])
def test_invalid_layers_cannot_produce_a_fused_result(fault):
    ctx, _ = _context()
    layer = ctx.inputs["fusion.layers"][0]
    if fault == "source":
        layer["source_sha256"] = "b" * 64
    elif fault == "promotion":
        layer["strength_class"] = "strong"
    elif fault == "duplicate":
        ctx.inputs["fusion.layers"].append(layer)
    else:
        layer["detector_id"] = "copymove.mask"
        ctx.inputs["fusion.unavailable"] = []
    with pytest.raises(InputRejectedError):
        run_native(ctx)


def test_absent_geometry_and_layers_emit_unavailable_not_zero_map():
    ctx, _ = _context()
    ctx.inputs["fusion.layers"] = []
    del ctx.inputs["image.geometry"]
    result = run_native(ctx)
    assert result["fusion.map"] is None and result["fusion.evidence"] is None
    assert result["fusion.score_info"]["status"] == "unavailable"
