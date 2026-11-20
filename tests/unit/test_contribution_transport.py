"""Accepted contributions bind actual scores and measured support without pixel JSON lists."""

from dataclasses import replace
from types import SimpleNamespace

import numpy as np
import pytest

from photoproof.contracts.contributions import Contribution, read_contributions, write_contributions
from photoproof.contracts.exceptions import InputRejectedError
from photoproof.contracts.fused_maps import FusedScores


def _transport(*, context=False):
    fused = FusedScores(
        np.array([[0, 0.8, 0], [0, 0.6, 0]], np.float32),
        np.array([[True, True, False], [True, True, False]]),
        "a" * 64,
        (12, 8),
    )
    scores = np.array([[0, 0.5, 0], [0, 0.4, 0]], np.float32)
    entry = Contribution(
        "compression.dq_map",
        "compression",
        "medium",
        0.6,
        "spatial",
        (),
        scores,
        fused.valid_mask.copy(),
    )
    if context:
        entry = replace(
            entry,
            detector_id="metadata.findings",
            family="metadata",
            strength_class="weak",
            effective_weight=0.3,
            scope="global_context",
            scores=scores * 0.1,
            measured=np.zeros(scores.shape, bool),
        )
    stored, calls = {}, []

    def writer(name, data, media_type):
        assert media_type == "application/x-npy"
        stored[name] = data
        return {"unit_test_ref": name}

    ctx = SimpleNamespace(inputs={}, write_artifact=writer)
    ctx.inputs["fusion.evidence"] = write_contributions(
        ctx, (entry,), fused=fused, unavailable=("copymove.mask",)
    )

    def reader(name, *, selector):
        calls.append((name, selector))
        root, ordinal, field = selector
        assert root == "contributions" and field in {"scores", "measured"}
        ref = ctx.inputs[name][root][ordinal][field]
        return stored[ref["unit_test_ref"]]

    ctx.artifact = reader
    geometry = {"source_sha256": "a" * 64, "working_size": [12, 8]}
    return ctx, fused, geometry, entry, calls


def test_roundtrip_preserves_score_attribution_and_measured_zero():
    ctx, fused, geometry, original, calls = _transport()
    result = read_contributions(ctx, geometry=geometry, fused=fused)
    assert len(result) == 1
    np.testing.assert_array_equal(result[0].scores, original.scores)
    np.testing.assert_array_equal(result[0].measured, original.measured)
    assert result[0].scores[0, 0] == 0 and result[0].measured[0, 0]
    assert not result[0].measured[0, 2]
    assert not result[0].scores.flags.writeable and not result[0].measured.flags.writeable
    assert calls == [
        ("fusion.evidence", ("contributions", 0, "scores")),
        ("fusion.evidence", ("contributions", 0, "measured")),
    ]


def test_global_context_never_becomes_measured_coverage():
    ctx, fused, geometry, _, _ = _transport(context=True)
    result = read_contributions(ctx, geometry=geometry, fused=fused)[0]
    assert result.scope == "global_context" and result.strength_class == "weak"
    assert result.scores.any() and not result.measured.any()


@pytest.mark.parametrize(
    "field,value",
    [
        ("source_sha256", "b" * 64),
        ("working_size", [13, 8]),
        ("grid_shape", [1, 3]),
        ("format_version", True),
        ("format_version", 1.0),
        ("calibration", "calibrated:imaginary"),
        ("coordinate_space", "guessed_pixels"),
        ("rule_version", "2.0.0"),
    ],
)
def test_bad_metadata_is_rejected_before_artifact_reads(field, value):
    ctx, fused, geometry, _, calls = _transport()
    ctx.inputs["fusion.evidence"][field] = value
    with pytest.raises(InputRejectedError):
        read_contributions(ctx, geometry=geometry, fused=fused)
    assert not calls


@pytest.mark.parametrize(
    "fault", ["weight", "coverage", "fused_score", "global_strength", "unavailable"]
)
def test_inconsistent_contributions_cannot_be_written(fault):
    ctx, fused, _, entry, _ = _transport()
    if fault == "weight":
        entry = replace(entry, effective_weight=0.0)
    elif fault == "coverage":
        entry = replace(entry, measured=np.zeros(entry.scores.shape, bool))
    elif fault == "fused_score":
        entry = replace(
            entry, scores=np.array([[0, 0.5, 0], [0, 0.7, 0]], np.float32), effective_weight=1.0
        )
    elif fault == "global_strength":
        entry = replace(entry, scope="global_context")
    absent = (entry.detector_id,) if fault == "unavailable" else ()
    with pytest.raises(InputRejectedError):
        write_contributions(ctx, (entry,), fused=fused, unavailable=absent)
