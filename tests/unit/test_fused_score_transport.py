"""Compact fused arrays retain source identity and explicit measured validity."""

import copy
from dataclasses import replace
from types import SimpleNamespace

import numpy as np
import pytest

from photoproof.contracts.exceptions import InputRejectedError
from photoproof.contracts.fused_maps import FusedScores, read_fused_scores, write_fused_scores


def _value():
    return FusedScores(
        np.array([[0, 0.8, 0]], dtype=np.float32),
        np.array([[True, True, False]]),
        "a" * 64,
        (12, 8),
    )


def _transport():
    stored, calls = {}, []

    def writer(name, data, media_type):
        assert media_type == "application/x-npy"
        stored[name] = data
        return {"unit_test_ref": name}

    ctx = SimpleNamespace(inputs={}, write_artifact=writer)

    def reader(name, *, selector):
        calls.append((name, selector))
        return stored[ctx.inputs[name][selector[0]]["unit_test_ref"]]

    ctx.artifact = reader
    ctx.inputs["fusion.map"] = write_fused_scores(ctx, _value())
    geometry = {"source_sha256": "a" * 64, "working_size": [12, 8]}
    return ctx, geometry, stored, calls


def test_round_trip_distinguishes_measured_zero_from_unmeasured_zero():
    ctx, geometry, _, calls = _transport()
    result = read_fused_scores(ctx, geometry=geometry)
    np.testing.assert_array_equal(result.scores, _value().scores)
    np.testing.assert_array_equal(result.valid_mask, [[True, True, False]])
    assert not result.scores.flags.writeable and not result.valid_mask.flags.writeable
    assert calls == [("fusion.map", ("scores",)), ("fusion.map", ("valid_mask",))]


@pytest.mark.parametrize(
    "field,value",
    [
        ("source_sha256", "b" * 64),
        ("working_size", [13, 8]),
        ("grid_shape", [10000, 10000]),
        ("format_version", True),
        ("format_version", 1.0),
        ("calibration", "calibrated:imaginary"),
        ("coordinate_space", "unknown"),
    ],
)
def test_invalid_provenance_is_rejected_before_artifact_reads(field, value):
    ctx, geometry, _, calls = _transport()
    ctx.inputs["fusion.map"][field] = value
    with pytest.raises(InputRejectedError):
        read_fused_scores(ctx, geometry=geometry)
    assert calls == []


@pytest.mark.parametrize(
    "field,value",
    [
        ("scores", np.array([[0, 0, 0]], dtype=np.float64)),
        ("scores", np.array([[0, 0.8, 1]], dtype=np.float32)),
        ("valid_mask", np.zeros((1, 3), dtype=bool)),
        ("valid_mask", np.ones((1, 3), dtype=np.uint8)),
        ("source_sha256", "invalid"),
        ("working_size", (2, 8)),
    ],
)
def test_invalid_scores_do_not_write_artifacts(field, value):
    ctx, _, _, _ = _transport()
    attempted = []
    ctx.write_artifact = lambda *args: attempted.append(args)
    with pytest.raises(InputRejectedError):
        write_fused_scores(ctx, replace(_value(), **{field: value}))
    assert attempted == []


def test_grid_metadata_cannot_reinterpret_a_payload_shape():
    ctx, geometry, _, _ = _transport()
    ctx.inputs["fusion.map"]["grid_shape"] = [3, 1]
    with pytest.raises(InputRejectedError, match="shape disagrees"):
        read_fused_scores(ctx, geometry=geometry)


def test_unavailable_is_not_an_available_zero_map():
    ctx, geometry, _, calls = _transport()
    ctx.inputs = {"fusion.map": None}
    assert read_fused_scores(ctx, geometry=geometry) is None
    assert calls == []


def test_unknown_record_fields_are_not_silently_ignored():
    ctx, geometry, _, calls = _transport()
    record = copy.deepcopy(ctx.inputs["fusion.map"])
    record["probability"] = 0.99
    ctx.inputs["fusion.map"] = record
    with pytest.raises(InputRejectedError):
        read_fused_scores(ctx, geometry=geometry)
    assert calls == []
