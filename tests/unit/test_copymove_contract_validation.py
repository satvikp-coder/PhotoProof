"""Copy-move records preserve source/coordinates and reject malformed relationships."""

import io
import json
from dataclasses import replace

import numpy as np
import pytest
from PIL import Image

from photoproof.contracts.copymove import (
    Cluster,
    Clusters,
    Keypoints,
    Mask,
    Matches,
    Region,
    Verified,
    VerifiedTransform,
)
from photoproof.contracts.exceptions import InputRejectedError

SOURCE = "a" * 64
BASE = {"source_sha256": SOURCE, "image_size": (64, 64)}


def _features():
    return Keypoints(
        **BASE,
        xy=np.array(
            [[1, 1], [2, 1], [1, 2], [2, 2], [20, 20], [21, 20], [20, 21], [21, 21]], np.float32
        ),
        scale=np.ones(8, np.float32),
        angle=np.zeros(8, np.float32),
        response=np.ones(8, np.float32),
        desc=np.zeros((8, 128), np.float32),
    )


def _matches():
    return Matches(
        **BASE,
        num_keypoints=8,
        pairs=np.array([[0, 4], [1, 5], [2, 6], [3, 7]], np.int32),
        distance=np.zeros(4, np.float32),
    )


def _cluster():
    return Cluster(
        id=0,
        pair_indices=(0, 1, 2, 3),
        source_bbox=(1, 1, 3, 3),
        target_bbox=(20, 20, 22, 22),
        mean_displacement=(19, 19),
    )


def _transform():
    return VerifiedTransform(
        cluster_id=0,
        matrix=np.array([[1, 0, 19], [0, 1, 19]], np.float64),
        inliers=4,
        rmse=0,
        scale=1,
        rotation_deg=0,
        score=0.8,
        flags=("ransac_verified",),
    )


def _mask():
    labels = np.zeros((64, 64), np.uint8)
    labels[1:3, 1:3] = 1
    labels[20:22, 20:22] = 2
    return Mask(
        **BASE,
        mask=labels,
        regions=(
            Region(kind="source", bbox=(1, 1, 3, 3), area=4),
            Region(kind="target", bbox=(20, 20, 22, 22), area=4),
        ),
    )


def test_all_five_formats_round_trip_with_explicit_source_and_context():
    models = [
        _features(),
        _matches(),
        Clusters(**BASE, clusters=(_cluster(),), num_matches=4),
        Verified(**BASE, transforms=(_transform(),), cluster_ids=(0,)),
        _mask(),
    ]
    for model in models:
        if isinstance(model, Clusters):
            payload = model.to_json()
            parsed = Clusters.from_json(payload, source_sha256=SOURCE)
            assert payload == parsed.to_json()
        else:
            payload = model.to_npz()
            parsed = type(model).from_npz(payload, source_sha256=SOURCE)
            assert payload == parsed.to_npz()
        assert parsed.source_sha256 == SOURCE and parsed.image_size == (64, 64)
        assert parsed.calibration == "uncalibrated" and parsed.coordinate_space == "working_pixels"


@pytest.mark.parametrize(
    "field,value",
    [
        ("desc", np.full((8, 128), np.nan)),
        ("xy", np.full((8, 2), 64)),
        ("scale", np.zeros(8)),
        ("response", np.full(8, np.inf)),
    ],
)
def test_keypoint_malformed_values_are_rejected(field, value):
    with pytest.raises(InputRejectedError):
        Keypoints(**{**_features().model_dump(), field: value})


@pytest.mark.parametrize(
    "pairs",
    [
        np.array([[-1, 1]], np.int32),
        np.array([[0, 8]], np.int32),
        np.array([[2**32, 0]], np.uint64),
        np.array([[0.5, 1]]),
    ],
)
def test_match_indices_cannot_be_negative_out_of_bounds_wrapped_or_truncated(pairs):
    with pytest.raises(InputRejectedError):
        Matches(**BASE, num_keypoints=8, pairs=pairs, distance=np.zeros(1, np.float32))


def test_cluster_and_verified_references_require_real_parent_indices():
    with pytest.raises(InputRejectedError, match="nonexistent match"):
        Clusters(**BASE, num_matches=3, clusters=(_cluster(),))
    with pytest.raises(InputRejectedError, match="nonexistent cluster"):
        Verified(**BASE, transforms=(_transform(),), cluster_ids=(99,))
    with pytest.raises(InputRejectedError, match="nonsingular"):
        replace(_transform(), matrix=np.zeros((2, 3)))


def test_source_mismatch_is_not_reinterpreted_as_current_image():
    with pytest.raises(InputRejectedError, match="source differs"):
        Keypoints.from_npz(_features().to_npz(), source_sha256="b" * 64)


def test_constructed_arrays_are_read_only_and_do_not_alias_the_callers_buffer():
    features = _features()
    values = np.array(features.xy)
    snapshot = Keypoints(**{**features.model_dump(), "xy": values})
    values[0, 0] = 40
    assert snapshot.xy[0, 0] == 1
    with pytest.raises(ValueError):
        snapshot.xy[0, 0] = 40


def test_mask_labels_cannot_wrap_to_background_and_regions_must_cover_pixels_once():
    with pytest.raises(InputRejectedError, match="wrapped"):
        Mask(source_sha256=SOURCE, image_size=(1, 1), mask=np.array([[256]], np.uint16), regions=())
    mask = _mask()
    with pytest.raises(InputRejectedError, match="lack region"):
        Mask(**{**mask.model_dump(), "regions": ()})
    with pytest.raises(InputRejectedError, match="twice"):
        Mask(**{**mask.model_dump(), "regions": (*mask.regions, mask.regions[0])})


def test_semantic_png_round_trip_preserves_all_labels_and_regions():
    mask = _mask()
    result = Mask.from_png(mask.to_png(), metadata=mask.to_json(), source_sha256=SOURCE)
    np.testing.assert_array_equal(result.mask, mask.mask)
    assert result.regions == mask.regions and not result.mask.flags.writeable
    assert set(np.unique(result.mask)) == {0, 1, 2}


def test_png_geometry_rejection_precedes_native_pixel_allocation(monkeypatch):
    mask = _mask()
    payload = bytearray(mask.to_png())
    payload[16:20] = (1000000).to_bytes(4, "big")

    def forbidden(*args, **kwargs):
        pytest.fail("invalid geometry must be rejected before PNG decoding")

    monkeypatch.setattr(Image, "open", forbidden)
    with pytest.raises(InputRejectedError, match="dimensions"):
        Mask.from_png(bytes(payload), metadata=mask.to_json())


@pytest.mark.parametrize(
    "payload",
    [
        '{"contract_version":"1.0.0"}',
        '{"contract_version":"2.0.0","num_matches":NaN}',
        '{"contract_version":"2.0.0","contract_version":"2.0.0"}',
    ],
)
def test_legacy_nonfinite_or_duplicate_metadata_is_rejected(payload):
    with pytest.raises(InputRejectedError):
        Clusters.from_json(payload)


def test_archive_unknown_metadata_cannot_add_a_probability_claim():
    features = _features()
    meta = {k: v for k, v in features.model_dump().items() if not isinstance(v, np.ndarray)}
    meta["probability"] = 0.99
    stream = io.BytesIO()
    np.savez(
        stream,
        metadata=np.frombuffer(json.dumps(meta).encode(), np.uint8),
        xy=features.xy,
        scale=features.scale,
        angle=features.angle,
        response=features.response,
        desc=features.desc,
    )
    with pytest.raises(InputRejectedError, match="contract fields"):
        Keypoints.from_npz(stream.getvalue())
