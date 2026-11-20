"""Synthetic wire controls; actual source images are covered by controller integration."""

import copy
import hashlib
import logging

import numpy as np
import pytest

from photoproof.contracts.arrays import encode_npy
from photoproof.contracts.artifacts import ArtifactRef
from photoproof.contracts.exceptions import InputRejectedError
from photoproof.contracts.plugins import PluginContext
from photoproof.contracts.registration import (
    inverse_coverage,
    read_registration,
    write_registration,
)
from photoproof.plugins.comparison.registration.schema import Config


def _geometry(sha):
    return {
        "schema_version": 1,
        "source_sha256": sha,
        "coordinate_convention": "zero_based_pixel_centres_xy",
        "working_size": [32, 24],
    }


def _case(*, failed=False):
    blobs, reads, writes = {}, [], []
    inputs = {}

    def put(name, payload, media="application/x-npy"):
        sha = hashlib.sha256(payload).hexdigest()
        blobs[sha] = payload
        return ArtifactRef(
            name=name,
            sha256=sha,
            size=len(payload),
            media_type=media,
            producer="comparison.registration",
        )

    def writer(name, payload, media):
        writes.append(name)
        return put(name, payload, media)

    def reader(name, maximum, selector):
        reads.append((name, selector))
        node = inputs[name]
        for part in selector:
            node = node[part]
        ref = (
            node if isinstance(node, ArtifactRef) else ArtifactRef.model_validate(node["artifact"])
        )
        data = blobs[ref.sha256]
        assert len(data) == ref.size and len(data) <= maximum
        assert hashlib.sha256(data).hexdigest() == ref.sha256
        return data

    ctx = PluginContext(
        run_id="transport-control",
        plugin_id="comparison.registration",
        inputs=inputs,
        config=None,
        seed=0,
        write_artifact=writer,
        read_artifact=reader,
        logger=logging.getLogger(__name__),
    )
    target, reference = _geometry("a" * 64), _geometry("b" * 64)
    matrix = np.array([[1, 0, 2], [0, 1, 1], [0, 0, 1.0]])
    valid = inverse_coverage(matrix, reference_size=(32, 24), target_size=(32, 24))
    aligned = np.zeros((24, 32, 3), np.uint8)
    aligned[valid] = 77
    x, y = np.meshgrid([4, 8, 12, 16], [4, 8, 12])
    points = np.column_stack([x.ravel(), y.ravel(), x.ravel() + 2, y.ravel() + 1]).astype(
        np.float32
    )
    outputs = write_registration(
        ctx,
        target_geometry=target,
        reference_geometry=reference,
        status="registration_failed" if failed else "registered",
        requested_model="affine",
        model=None if failed else "affine",
        matrix=None if failed else matrix,
        aligned_reference=None if failed else aligned,
        overlap_mask=None if failed else valid,
        inlier_points=None if failed else points,
        quality={"reason": "insufficient_matches"}
        if failed
        else {"inliers": 12, "matched_points": 12, "inlier_ratio": 1.0, "reprojection_rmse": 0.0},
        parameters=Config(model="affine").model_dump(),
    )
    inputs.update(outputs)
    return ctx, target, reference, put, reads, writes


def test_typed_roundtrip_preserves_source_roles_and_readonly_arrays():
    ctx, target, reference, _, reads, writes = _case()
    decoded = read_registration(ctx, target_geometry=target, reference_geometry=reference)
    assert decoded.model == "affine" and decoded.inlier_points.shape == (12, 4)
    assert decoded.aligned_reference.shape == (24, 32, 3)
    assert not decoded.aligned_reference[~decoded.overlap_mask].any()
    assert all(
        not value.flags.writeable
        for value in (
            decoded.matrix,
            decoded.aligned_reference,
            decoded.overlap_mask,
            decoded.inlier_points,
        )
    )
    assert len(reads) == len(writes) == 3
    assert (
        ctx.inputs["comparison.transform"]["target_sha256"]
        != ctx.inputs["comparison.transform"]["reference_sha256"]
    )


def test_failure_has_no_artifacts_and_never_reads_pixels():
    ctx, target, reference, _, reads, writes = _case(failed=True)
    assert read_registration(ctx, target_geometry=target, reference_geometry=reference) is None
    assert not reads and not writes
    assert ctx.inputs["comparison.aligned_reference"] is None


@pytest.mark.parametrize(
    "field,value",
    [
        ("target_sha256", "c" * 64),
        ("reference_sha256", "a" * 64),
        ("target_size", [24, 32]),
        ("coordinate_convention", "pixel_corners"),
        ("status", []),
        ("requested_model", {}),
        ("model", {}),
        ("matrix", [[True, 0, 2], [0, 1, 1], [0, 0, 1]]),
        ("matrix", [[float("inf"), 0, 2], [0, 1, 1], [0, 0, 1]]),
    ],
)
def test_invalid_metadata_rejected_before_artifact_reads(field, value):
    ctx, target, reference, _, reads, _ = _case()
    ctx.inputs["comparison.transform"][field] = value
    with pytest.raises(InputRejectedError):
        read_registration(ctx, target_geometry=target, reference_geometry=reference)
    assert not reads


@pytest.mark.parametrize(
    "kind", ["outside_mask", "duplicate_inlier", "outside_inlier", "wrong_dtype"]
)
def test_verified_payload_hashes_do_not_replace_semantic_validation(kind):
    ctx, target, reference, put, _, _ = _case()
    value = read_registration(ctx, target_geometry=target, reference_geometry=reference)
    if kind == "outside_mask":
        name, array = "overlap_mask", value.overlap_mask.copy()
        array[0, 0] = True
    elif kind == "wrong_dtype":
        name, array = "aligned_reference", value.aligned_reference.astype(np.float32)
    else:
        name, array = "inlier_points", value.inlier_points.copy()
        array[1] = array[0] if kind == "duplicate_inlier" else [100, 100, 102, 101]
    ctx.inputs["comparison.transform"]["artifacts"][name] = put(name, encode_npy(array))
    with pytest.raises(InputRejectedError):
        read_registration(ctx, target_geometry=target, reference_geometry=reference)


@pytest.mark.parametrize(
    "field,value",
    [("inliers", 13), ("inlier_ratio", 0.25), ("reprojection_rmse", 1.0), ("matched_points", True)],
)
def test_quality_is_bound_to_actual_saved_inlier_points(field, value):
    ctx, target, reference, _, _, _ = _case()
    ctx.inputs["comparison.transform"]["quality"][field] = value
    with pytest.raises(InputRejectedError):
        read_registration(ctx, target_geometry=target, reference_geometry=reference)


def test_missing_companions_and_foreign_artifacts_are_not_read():
    ctx, target, reference, _, reads, _ = _case()
    artifacts = ctx.inputs["comparison.transform"]["artifacts"]
    original = copy.copy(artifacts)
    del artifacts["overlap_mask"]
    with pytest.raises(InputRejectedError):
        read_registration(ctx, target_geometry=target, reference_geometry=reference)
    assert not reads
    artifacts.update(original)
    artifacts["aligned_reference"] = artifacts["aligned_reference"].model_copy(
        update={"producer": "support.preview"}
    )
    with pytest.raises(InputRejectedError):
        read_registration(ctx, target_geometry=target, reference_geometry=reference)
    assert not reads


def test_failed_record_cannot_hide_registered_payloads():
    ctx, target, reference, _, reads, _ = _case()
    ctx.inputs["comparison.transform"]["status"] = "registration_failed"
    with pytest.raises(InputRejectedError):
        read_registration(ctx, target_geometry=target, reference_geometry=reference)
    assert not reads


def test_artifact_size_budget_is_checked_before_broker_reads():
    ctx, target, reference, _, reads, _ = _case()
    artifacts = ctx.inputs["comparison.transform"]["artifacts"]
    artifacts["aligned_reference"] = artifacts["aligned_reference"].model_copy(
        update={"size": 1 << 30}
    )
    with pytest.raises(InputRejectedError, match="budget"):
        read_registration(ctx, target_geometry=target, reference_geometry=reference)
    assert not reads


def test_coverage_budget_precedes_grid_allocation(monkeypatch):
    matrix = np.eye(3)

    def forbidden(*args, **kwargs):
        raise AssertionError("coverage grid allocated before admission")

    monkeypatch.setattr(np, "zeros", forbidden)
    with pytest.raises(InputRejectedError, match="admitted working grid"):
        inverse_coverage(matrix, reference_size=(32, 24), target_size=(4194305, 1))


def test_large_reproducibility_seed_is_preserved_exactly():
    ctx, target, reference, _, _, _ = _case()
    seed = (1 << 64) - 1
    ctx.inputs["comparison.transform"]["quality"]["seed"] = seed
    result = read_registration(ctx, target_geometry=target, reference_geometry=reference)
    assert type(result.quality["seed"]) is int and result.quality["seed"] == seed
