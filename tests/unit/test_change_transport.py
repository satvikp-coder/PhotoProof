"""Actual numerical transport plus generated malformed derived-mask controls."""

import hashlib
import io
import logging
import struct
import zlib

import numpy as np
import pytest
from PIL import Image

from photoproof.contracts.arrays import encode_npy
from photoproof.contracts.artifacts import ArtifactRef
from photoproof.contracts.changes import encode_change_mask, read_changes
from photoproof.contracts.exceptions import InputRejectedError
from photoproof.contracts.plugins import PluginContext
from photoproof.plugins.comparison.change_mask.processor import run
from photoproof.plugins.comparison.change_mask.schema import Config
from tests.unit.test_similarity_transport import _case as similarity_case


def _case(*, empty=False):
    upstream, target, reference, _, _, _ = similarity_case()
    inputs = dict(upstream.inputs)
    inputs.update({"image.geometry": target, "image.reference_geometry": reference})
    blobs, reads = {}, []

    def put(name, data, media="application/x-npy"):
        sha = hashlib.sha256(data).hexdigest()
        blobs[sha] = data
        return ArtifactRef(
            name=name,
            sha256=sha,
            media_type=media,
            size=len(data),
            producer="comparison.change_mask",
        )

    def reader(name, maximum, selector):
        reads.append((name, selector))
        if name != "comparison.change_regions":
            return upstream.artifact(name, max_bytes=maximum, selector=selector)
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
        plugin_id="comparison.change_mask",
        inputs=inputs,
        config=Config(threshold=1 if empty else 0.15),
        seed=0,
        write_artifact=put,
        read_artifact=reader,
        logger=logging.getLogger(__name__),
    )
    inputs.update(run(ctx))
    reads.clear()
    return ctx, target, reference, put, reads


def test_roundtrip_verifies_real_labels_png_and_statistics():
    ctx, target, reference, _, reads = _case()
    record, labels = read_changes(ctx, target_geometry=target, reference_geometry=reference)
    assert labels.dtype == np.int32 and labels.shape == (24, 32)
    assert not labels.flags.writeable
    assert record.facts["changed_pixels"] == np.count_nonzero(labels)
    assert record.regions[0].category == "difference"
    assert record.regions[0].mean_dissimilarity == pytest.approx(0.3)
    assert ("comparison.change_regions", ("mask",)) in reads


def test_empty_is_measured_at_explicit_sensitivity_not_unavailable():
    ctx, target, reference, _, _ = _case(empty=True)
    record, labels = read_changes(ctx, target_geometry=target, reference_geometry=reference)
    assert not labels.any() and not record.regions
    assert record.facts["status"] == "no_differences_detected"


@pytest.mark.parametrize(
    "field,value",
    [
        ("format_version", True),
        ("target_sha256", "f" * 64),
        ("reference_sha256", "a" * 64),
        ("target_size", [32, 25]),
        ("similarity_binding", "0" * 64),
        ("coordinate_convention", "decoded"),
        ("unexpected", 1),
    ],
)
def test_invalid_metadata_fails_before_reads(field, value):
    ctx, target, reference, _, reads = _case()
    ctx.inputs["comparison.change_regions"][field] = value
    with pytest.raises(InputRejectedError):
        read_changes(ctx, target_geometry=target, reference_geometry=reference)
    assert not reads


@pytest.mark.parametrize("kind", ["producer", "oversize", "media"])
def test_companion_admission_before_upstream_or_pixel_reads(kind):
    ctx, target, reference, _, reads = _case()
    record = ctx.inputs["comparison.change_regions"]
    update = (
        {"producer": "other"}
        if kind == "producer"
        else {"size": 100000000}
        if kind == "oversize"
        else {"media_type": "image/jpeg"}
    )
    record["mask"] = record["mask"].model_copy(update=update)
    with pytest.raises(InputRejectedError):
        read_changes(ctx, target_geometry=target, reference_geometry=reference)
    assert not reads


@pytest.mark.parametrize(
    "field,value",
    [
        ("area", 1),
        ("bbox", [0, 0, 1, 1]),
        ("mean_dissimilarity", 0.99),
        ("category", "manipulation"),
        ("area_fraction", 1.0),
        ("id", 1000),
    ],
)
def test_region_numbers_or_category_cannot_disagree_with_measured_raster(field, value):
    ctx, target, reference, _, _ = _case()
    ctx.inputs["comparison.change_regions"]["regions"][0][field] = value
    with pytest.raises(InputRejectedError):
        read_changes(ctx, target_geometry=target, reference_geometry=reference)


@pytest.mark.parametrize(
    "field,value",
    [
        ("changed_pixels", 0),
        ("region_count", 0),
        ("reported_region_count", False),
        ("measured_pixels", "260"),
        ("target_pixels", 10),
        ("status", "no_differences_detected"),
        ("regions_truncated", True),
        ("low_specificity", False),
    ],
)
def test_summary_counts_flags_and_status_cannot_disagree_with_measurements(field, value):
    ctx, target, reference, _, _ = _case()
    ctx.inputs["comparison.change_regions"]["facts"][field] = value
    with pytest.raises(InputRejectedError):
        read_changes(ctx, target_geometry=target, reference_geometry=reference)


@pytest.mark.parametrize("kind", ["dtype", "negative", "huge", "shape", "unknown_support"])
def test_malformed_labels_do_not_become_differences(kind):
    ctx, target, reference, put, _ = _case()
    labels = np.zeros((24, 32), np.int32)
    if kind == "dtype":
        labels = labels.astype(np.float32)
    elif kind == "negative":
        labels[10, 10] = -1
    elif kind == "huge":
        labels[10, 10] = 769
    elif kind == "shape":
        labels = labels[:-1]
    else:
        labels[0, 0] = 1
    ctx.inputs["comparison.change_regions"]["labels"] = put("corrupt.npy", encode_npy(labels))
    with pytest.raises(InputRejectedError):
        read_changes(ctx, target_geometry=target, reference_geometry=reference)


def _png(payload):
    def chunk(kind, data):
        return (
            struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data))
        )

    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", struct.pack(">IIBBBBB", 32, 24, 8, 0, 0, 0, 0))
        + chunk(b"IDAT", payload)
        + chunk(b"IEND", b"")
    )


@pytest.mark.parametrize(
    "kind", ["pixels", "checksum", "compression", "inflate_budget", "trailing", "truncated"]
)
def test_derived_png_is_bounded_and_must_match_label_pixels(kind):
    ctx, target, reference, put, _ = _case()
    data = encode_change_mask(np.zeros((24, 32), bool))
    if kind == "checksum":
        data = data[:-1] + bytes([data[-1] ^ 1])
    elif kind == "compression":
        data = _png(b"invalid compression")
    elif kind == "inflate_budget":
        data = _png(zlib.compress(b"\0" * 100000))
    elif kind == "trailing":
        data += b"trailing"
    elif kind == "truncated":
        data = data[:20]
    ctx.inputs["comparison.change_regions"]["mask"] = put("bad.png", data, "image/png")
    with pytest.raises(InputRejectedError):
        read_changes(ctx, target_geometry=target, reference_geometry=reference)


def test_minimal_png_is_readable_by_independent_decoder():
    mask = np.zeros((19, 31), bool)
    mask[2:10, 5:13] = True
    with Image.open(io.BytesIO(encode_change_mask(mask))) as image:
        assert image.mode == "L" and image.size == (31, 19)
        np.testing.assert_array_equal(np.asarray(image) > 0, mask)


def test_encoder_rejects_budget_before_allocating_scanlines():
    mask = np.broadcast_to(np.zeros((1, 1), bool), (4194305, 1))
    with pytest.raises(InputRejectedError):
        encode_change_mask(mask)


def test_missing_changes_are_unavailable_without_reads():
    ctx, target, reference, _, reads = _case()
    del ctx.inputs["comparison.change_regions"]
    assert read_changes(ctx, target_geometry=target, reference_geometry=reference) is None
    assert not reads
