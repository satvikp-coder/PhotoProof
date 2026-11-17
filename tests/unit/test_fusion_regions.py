"""Deterministic localization, traceability, artifact and failure checks for #35."""

import hashlib
import io
import logging

import numpy as np
import pytest
from PIL import Image
from pydantic import ValidationError

from photoproof.contracts.artifacts import ArtifactRef
from photoproof.contracts.exceptions import InputRejectedError
from photoproof.contracts.plugins import PluginContext
from photoproof.plugins.fusion.regions.algorithm import fill_holes, hysteresis, segment
from photoproof.plugins.fusion.regions.processor import run
from photoproof.plugins.fusion.regions.schema import Config
from tools.metrics import iou


def test_hysteresis_connected_growth():
    scores = np.zeros((8, 8), dtype=np.float32)
    scores[2, 2] = 0.9
    scores[2, 3:6] = 0.4
    scores[6, 6] = 0.4
    mask = hysteresis(scores, high=0.7, low=0.3)
    assert mask.sum() == 4
    assert mask[2, 5] == 1
    assert mask[6, 6] == 0


@pytest.mark.parametrize("touch_border", [False, True])
def test_fill_enclosed_holes_only(touch_border):
    mask = np.zeros((12, 12), dtype=np.uint8)
    start = 0 if touch_border else 2
    mask[start:10, start:10] = 1
    mask[5, 5] = 0
    result = fill_holes(mask)
    assert result[5, 5] == 1
    assert result[11, 11] == 0
    assert mask[5, 5] == 0


def test_planted_blobs_and_speckle():
    truth = np.zeros((100, 100), dtype=np.uint8)
    truth[10:40, 10:40] = 1
    truth[60:90, 60:90] = 1
    scores = truth.astype(np.float32) * 0.9
    scores[4, 4] = scores[50, 50] = 0.95
    scores.flags.writeable = False
    evidence = []
    for y, x, detector in [(20, 20, "noise.consistency_map"), (70, 70, "copymove.mask")]:
        evidence.append(
            {"region": {"rows": [y], "cols": [x]}, "contributing_detectors": [detector]}
        )
    evidence.append({"reason": "unavailable", "detector_id": "missing"})
    mask, regions, summary = segment(scores, evidence=evidence, config=Config())
    assert iou(truth.tolist(), mask.tolist()) >= 0.9
    assert mask[4, 4] == mask[50, 50] == 0
    assert len(regions) == 2
    assert {tuple(r["contributing_detectors"]) for r in regions} == {
        ("noise.consistency_map",),
        ("copymove.mask",),
    }
    assert all(r["area"] > 850 and r["max_score"] > 0.89 for r in regions)
    assert all(len(r["polygon"]) >= 3 for r in regions)
    assert summary["calibration"] == "uncalibrated"
    assert not summary["truncated"]


def test_area_filter_and_count_cap_preserve_mask():
    scores = np.zeros((20, 20), dtype=np.float32)
    scores[1:4, 1:4] = 0.9
    scores[10:13, 10:13] = 0.9
    scores[18, 18] = 0.9
    mask, regions, summary = segment(
        scores, evidence=[], config=Config(kernel_size=1, min_area_fraction=0.01, max_regions=1)
    )
    assert mask.sum() == 18
    assert len(regions) == 1
    assert summary["region_count"] == 2 and summary["truncated"]


def test_full_image_is_flagged():
    mask, regions, summary = segment(
        np.ones((10, 10), dtype=np.float32), evidence=[], config=Config()
    )
    assert mask.sum() == 100
    assert regions[0]["full_image"] and summary["full_image"]
    assert regions[0]["bbox"] == [0, 0, 10, 10]


@pytest.mark.parametrize(
    "scores",
    [
        np.array([0.8]),
        np.zeros((0, 2)),
        np.array([[np.nan]]),
        np.array([[np.inf]]),
        np.array([[-0.1]]),
        np.array([[1.1]]),
    ],
)
def test_invalid_maps_fail_visibly(scores):
    with pytest.raises(InputRejectedError, match="finite 2-D grid"):
        segment(scores, evidence=[], config=Config())


@pytest.mark.parametrize(
    "config",
    [
        {"low": 0.9},
        {"kernel_size": 2},
        {"max_regions": 1000},
        {"high": float("nan")},
        {"polygon_tolerance": float("inf")},
    ],
)
def test_invalid_config(config):
    with pytest.raises(ValidationError):
        Config(**config)


@pytest.mark.parametrize("value", [None, [], [[0.0] * 8] * 8, [[0.9] * 8] * 8])
def test_processor_artifact_and_empty_status(value):
    written = []

    def writer(name, data, media_type):
        written.append(data)
        return ArtifactRef(
            name=name,
            sha256=hashlib.sha256(data).hexdigest(),
            size=len(data),
            media_type=media_type,
            producer="fusion.regions",
        )

    evidence = [{"reason": "unavailable", "detector_id": "missing"}]
    ctx = PluginContext(
        run_id="r",
        plugin_id="fusion.regions",
        inputs={"fusion.map": value, "fusion.evidence": evidence},
        config=Config(),
        seed=0,
        write_artifact=writer,
        logger=logging.getLogger("test"),
    )
    result = run(ctx)
    assert set(result) == {"fusion.mask", "fusion.regions", "fusion.segmentation"}
    assert evidence == [{"reason": "unavailable", "detector_id": "missing"}]
    if value is None or value == []:
        assert result["fusion.mask"] is None and not written
        assert result["fusion.segmentation"]["status"] == "unavailable"
    else:
        assert isinstance(result["fusion.mask"], ArtifactRef)
        with Image.open(io.BytesIO(written[0])) as image:
            pixels = np.array(image)
        assert pixels.shape == (8, 8)
        assert set(np.unique(pixels)) <= {0, 255}
        assert pixels.any() == (value[0][0] > 0)
