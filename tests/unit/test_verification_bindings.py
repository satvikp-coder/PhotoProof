"""Exact source-bound artifact identities are required for downstream row interpretation."""

import hashlib
import json

import pytest

from photoproof.contracts.copymove import VerificationBindings
from photoproof.contracts.exceptions import InputRejectedError


def _bindings():
    values = {
        name: name.encode() for name in ("keypoints", "matches", "clusters", "verified", "inliers")
    }
    binding = VerificationBindings(
        source_sha256="a" * 64,
        image_size=(512, 512),
        geometric_model="similarity",
        reproj_threshold=3,
        min_inliers=6,
        seed=23,
        ransac_stop_confidence=0.99,
        **{name + "_sha256": hashlib.sha256(value).hexdigest() for name, value in values.items()},
    )
    return binding, values


def test_bindings_round_trip_and_each_original_payload_is_checked():
    binding, values = _bindings()
    result = VerificationBindings.from_json(binding.to_json(), source_sha256="a" * 64)
    assert result == binding and result.calibration == "uncalibrated"
    for kind, payload in values.items():
        result.verify_payload(kind, payload)
        with pytest.raises(InputRejectedError, match="bytes differ"):
            result.verify_payload(kind, payload + b"different")
    with pytest.raises(InputRejectedError, match="source differs"):
        VerificationBindings.from_json(binding.to_json(), source_sha256="b" * 64)


@pytest.mark.parametrize(
    "field,value",
    [
        ("matches_sha256", "bad"),
        ("keypoints_sha256", "B" * 64),
        ("calibration", "probability"),
        ("probability", 0.99),
        ("min_inliers", True),
        ("reproj_threshold", float("nan")),
    ],
)
def test_malformed_or_probability_bindings_are_not_accepted(field, value):
    binding, _ = _bindings()
    document = {**json.loads(binding.to_json()), field: value}
    with pytest.raises(InputRejectedError):
        VerificationBindings.from_json(json.dumps(document))


def test_binding_kind_cannot_be_a_path_or_arbitrary_capability():
    binding, _ = _bindings()
    with pytest.raises(InputRejectedError, match="no filesystem path"):
        binding.verify_payload("../secret", b"anything")
