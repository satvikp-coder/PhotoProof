"""Geometry describes the transformation actually performed, including policy."""

from dataclasses import replace

import numpy as np
import pytest

from photoproof.contracts.exceptions import InputRejectedError
from photoproof.core.image_geometry import describe_geometry
from photoproof.imaging.decoder import DecodedImage
from photoproof.imaging.normalization import NormalizationPolicy, normalize


def _decoded(orientation):
    return DecodedImage(
        source_sha256="a" * 64,
        media_format="png",
        width=4,
        height=3,
        channels=3,
        bit_depth=8,
        pixels=np.arange(36, dtype=np.uint8).tobytes(),
        orientation=orientation,
        mode="RGB",
    )


def test_disabled_orientation_is_identity_even_with_rotation_metadata():
    decoded = _decoded(6)
    working = normalize(decoded, NormalizationPolicy(apply_orientation=False))
    geometry = describe_geometry(decoded, working)
    assert geometry["source_orientation"] == 6
    assert geometry["applied_orientation"] == 1
    assert geometry["working_size"] == [4, 3]
    assert geometry["decoded_to_working"] == [[1, 0, 0], [0, 1, 0], [0, 0, 1]]


def test_ignored_invalid_orientation_retains_explicit_provenance():
    decoded = _decoded(9)
    geometry = describe_geometry(decoded, normalize(decoded))
    assert geometry["source_orientation"] == 9
    assert geometry["applied_orientation"] == 1
    assert "orientation_invalid:9" in geometry["normalization_transforms"]


def test_inconsistent_working_dimensions_are_rejected():
    decoded = _decoded(6)
    with pytest.raises(InputRejectedError, match="disagrees"):
        describe_geometry(decoded, replace(normalize(decoded), width=100))
