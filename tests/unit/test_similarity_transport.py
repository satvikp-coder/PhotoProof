"""Wire corruption controls; real image comparison lives in controller tests."""

import copy
import hashlib
import logging

import numpy as np
import pytest

from photoproof.contracts.arrays import encode_npy
from photoproof.contracts.artifacts import ArtifactRef
from photoproof.contracts.exceptions import InputRejectedError
from photoproof.contracts.plugins import PluginContext
from photoproof.contracts.similarity import read_similarity, write_similarity


def _case():
    from tests.unit.test_registration_transport import _case as registration_case

    reg_ctx, target, reference, _, _, _ = registration_case()
    inputs = {"comparison.transform": reg_ctx.inputs["comparison.transform"]}
    blobs, reads, writes = {}, [], []

    def put(name, data, media="application/x-npy", producer="comparison.ssim"):
        sha = hashlib.sha256(data).hexdigest()
        blobs[sha] = data
        return ArtifactRef(
            name=name, sha256=sha, size=len(data), media_type=media, producer=producer
        )

    def writer(name, data, media):
        writes.append(name)
        return put(name, data, media)

    def reader(name, maximum, selector):
        if name == "comparison.transform":
            return reg_ctx.artifact(name, max_bytes=maximum, selector=selector)
        reads.append(selector)
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
        run_id="wire",
        plugin_id="comparison.ssim",
        inputs=inputs,
        config=None,
        seed=0,
        write_artifact=writer,
        read_artifact=reader,
        logger=logging.getLogger(__name__),
    )
    valid = np.zeros((24, 32), bool)
    valid[6:-5, 7:-5] = True
    difference = valid.astype(np.float32) * 0.3
    absolute = valid.astype(np.float32) * 0.1
    suppressed = np.zeros_like(valid)
    outputs = write_similarity(
        ctx,
        target_geometry=target,
        reference_geometry=reference,
        registration=inputs["comparison.transform"],
        dissimilarity=difference,
        abs_difference=absolute,
        valid_mask=valid,
        edge_suppressed=suppressed,
        facts={"global_ssim": 0.4},
    )
    inputs.update(outputs)
    return ctx, target, reference, put, reads, writes


def test_roundtrip_has_explicit_support_and_readonly_arrays():
    ctx, target, reference, _, reads, writes = _case()
    result = read_similarity(ctx, target_geometry=target, reference_geometry=reference)
    assert result.dissimilarity.shape == (24, 32)
    assert result.valid_mask.sum() == 13 * 20
    assert len(reads) == len(writes) == 4
    assert all(
        not a.flags.writeable
        for a in (
            result.dissimilarity,
            result.abs_difference,
            result.valid_mask,
            result.edge_suppressed,
        )
    )


@pytest.mark.parametrize(
    "field,value",
    [
        ("format_version", True),
        ("target_sha256", "f" * 64),
        ("reference_sha256", "a" * 64),
        ("target_size", [32, 25]),
        ("status", "unavailable"),
        ("registration_binding", "0" * 64),
        ("coordinate_convention", "decoded"),
        ("extra", "unknown"),
    ],
)
def test_bad_summary_rejected_before_read(field, value):
    ctx, target, reference, _, reads, _ = _case()
    ctx.inputs["comparison.similarity_summary"][field] = value
    with pytest.raises(InputRejectedError):
        read_similarity(ctx, target_geometry=target, reference_geometry=reference)
    assert not reads


@pytest.mark.parametrize(
    "kind", ["matrix", "role", "missing", "producer", "oversize", "media", "frame"]
)
def test_upstream_or_companion_substitution_rejected_before_reads(kind):
    ctx, target, reference, _, reads, _ = _case()
    summary = ctx.inputs["comparison.similarity_summary"]
    if kind == "matrix":
        ctx.inputs["comparison.transform"]["matrix"][0][2] += 1
    elif kind == "role":
        ctx.inputs["comparison.transform"]["reference_sha256"] = "a" * 64
    elif kind == "missing":
        del summary["artifacts"]["similarity_validity"]
    elif kind == "frame":
        target = copy.deepcopy(target)
        target["working_size"] = [100000, 100000]
    else:
        ref = summary["artifacts"]["edge_suppression"]
        update = (
            {"producer": "other"}
            if kind == "producer"
            else {"size": 10000000}
            if kind == "oversize"
            else {"media_type": "image/png"}
        )
        summary["artifacts"]["edge_suppression"] = ref.model_copy(update=update)
    with pytest.raises(InputRejectedError):
        read_similarity(ctx, target_geometry=target, reference_geometry=reference)
    assert not reads


@pytest.mark.parametrize(
    "kind", ["nan", "range", "dtype", "outside", "empty", "suppressed", "shape"]
)
def test_payload_contradictions_rejected(kind):
    ctx, target, reference, put, _, _ = _case()
    name = "dissimilarity_map"
    array = np.zeros((24, 32), np.float32)
    if kind == "nan":
        array[10, 10] = np.nan
    elif kind == "range":
        array[10, 10] = 1.1
    elif kind == "dtype":
        array = array.astype(np.float64)
    elif kind == "outside":
        array[0, 0] = 0.1
    elif kind == "shape":
        array = array[:-1]
    else:
        name = "similarity_validity" if kind == "empty" else "edge_suppression"
        array = np.zeros((24, 32), bool)
        if kind == "suppressed":
            array[0, 0] = True
    ctx.inputs["comparison.similarity_summary"]["artifacts"][name] = put(
        name + ".npy", encode_npy(array)
    )
    with pytest.raises(InputRejectedError):
        read_similarity(ctx, target_geometry=target, reference_geometry=reference)


def test_missing_summary_is_unavailable_without_reads():
    ctx, target, reference, _, reads, _ = _case()
    del ctx.inputs["comparison.similarity_summary"]
    assert read_similarity(ctx, target_geometry=target, reference_geometry=reference) is None
    assert not reads


@pytest.mark.parametrize("position", [(0, 0), (5, 5), (10, 5)])
def test_validity_cannot_expand_beyond_actual_registered_window_support(position):
    ctx, target, reference, put, _, _ = _case()
    valid = np.zeros((24, 32), bool)
    valid[position] = True
    for name in ("dissimilarity_map", "abs_diff_map"):
        ctx.inputs["comparison.similarity_summary"]["artifacts"][name] = put(
            name + ".npy", encode_npy(np.zeros(valid.shape, np.float32))
        )
    record = ctx.inputs["comparison.similarity_summary"]
    record["facts"]["global_ssim"] = 1.0
    record["artifacts"]["similarity_validity"] = put("valid.npy", encode_npy(valid))
    with pytest.raises(InputRejectedError, match="fully measured"):
        read_similarity(ctx, target_geometry=target, reference_geometry=reference)


@pytest.mark.parametrize(
    "field,value",
    [
        ("global_ssim", 0.99),
        ("global_ssim", float("nan")),
        ("valid_fraction", 1.0),
        ("edge_suppressed_pixels", 50),
    ],
)
def test_summary_numbers_cannot_contradict_measured_arrays(field, value):
    ctx, target, reference, _, _, _ = _case()
    ctx.inputs["comparison.similarity_summary"]["facts"][field] = value
    with pytest.raises(InputRejectedError, match="contradict"):
        read_similarity(ctx, target_geometry=target, reference_geometry=reference)
