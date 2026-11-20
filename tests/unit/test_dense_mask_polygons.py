"""Pixel-center rings retain holes and actual pixel areas, not bounding-box areas."""

import numpy as np
import pytest

from photoproof.contracts.copymove import Mask, Region
from photoproof.contracts.exceptions import InputRejectedError
from photoproof.plugins.copymove.mask.regions import polygons


def test_component_hole_and_disconnected_island_have_exact_areas():
    labels = np.zeros((30, 40), np.uint8)
    labels[2:20, 3:22] = 1
    labels[6:16, 7:18] = 0
    labels[9:12, 10:13] = 1
    labels[22:28, 29:37] = 2
    mask = Mask(
        source_sha256="a" * 64,
        image_size=(40, 30),
        mask=labels,
        regions=(
            Region(kind="source", bbox=(2, 3, 20, 22), area=int((labels == 1).sum())),
            Region(kind="target", bbox=(22, 29, 28, 37), area=48),
        ),
    )
    result = polygons(mask)
    assert len(result) == 3
    assert sum(r["area_pixels"] for r in result) == np.count_nonzero(labels)
    assert len([r for r in result if r["holes"]]) == 1
    assert all(r["outer"] for r in result)


def test_excess_components_reject_before_serialization():
    labels = np.zeros((100, 100), np.uint8)
    labels[::2, ::2] = 1
    mask = Mask(
        source_sha256="a" * 64,
        image_size=(100, 100),
        mask=labels,
        regions=(Region(kind="source", bbox=(0, 0, 99, 99), area=2500),),
    )
    with pytest.raises(InputRejectedError, match="thousand"):
        polygons(mask)
