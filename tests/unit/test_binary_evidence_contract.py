"""Binary layer contracts reject missing evidence and unsupported forensic claims."""

import hashlib
import logging
from dataclasses import replace

import numpy as np
import pytest

from photoproof.contracts.artifacts import ArtifactRef
from photoproof.contracts.evidence import EvidenceLayer
from photoproof.contracts.evidence_io import SpatialEvidence, write_evidence_layer
from photoproof.contracts.exceptions import InputRejectedError
from photoproof.contracts.grids import SamplingGrid
from photoproof.contracts.plugins import PluginContext


def _evidence():
    return SpatialEvidence(
        EvidenceLayer(
            detector_id="noise.consistency_map",
            map=np.full((2, 3), 0.4, dtype=np.float32),
            regions=(),
            strength_class="medium",
            valid_mask=np.ones((2, 3), dtype=bool),
            calibration="uncalibrated",
        ),
        "a" * 64,
        SamplingGrid((3, 2), (0, 0), (1, 1), (1, 1)),
    )


def _context(written):
    def writer(name, data, media_type):
        written.append(data)
        return ArtifactRef(
            name=name,
            sha256=hashlib.sha256(data).hexdigest(),
            size=len(data),
            media_type=media_type,
            producer="probe.writer",
        )

    return PluginContext(
        run_id="binary",
        plugin_id="probe.writer",
        inputs={},
        config=None,
        seed=0,
        logger=logging.getLogger(__name__),
        write_artifact=writer,
    )


@pytest.mark.parametrize(
    "field,value",
    [
        ("map", None),
        ("map", np.full((2, 3), float("nan"))),
        ("map", np.full((2, 3), 1.1)),
        ("valid_mask", np.zeros((2, 3), dtype=bool)),
        ("valid_mask", np.ones((2, 3), dtype=np.uint8)),
        ("calibration", "calibrated:imaginary-dataset"),
        ("strength_class", "probability"),
        ("notes", ("x" * 4097,)),
        ("regions", ((0, 0, 100, 100),)),
    ],
)
def test_invalid_layers_never_write_artifacts(field, value):
    evidence = _evidence()
    evidence = replace(evidence, layer=replace(evidence.layer, **{field: value}))
    written = []
    with pytest.raises(InputRejectedError):
        write_evidence_layer(_context(written), evidence, ordinal=0)
    assert not written


def test_explicit_global_context_cannot_turn_into_strong_localization():
    evidence = _evidence()
    evidence = replace(
        evidence,
        sampling_grid=None,
        layer=replace(
            evidence.layer,
            map=np.ones((1, 1), dtype=np.float32),
            valid_mask=np.ones((1, 1), dtype=bool),
            strength_class="strong",
        ),
    )
    written = []
    with pytest.raises(InputRejectedError, match="global context"):
        write_evidence_layer(_context(written), evidence, ordinal=0)
    assert not written


def test_global_zero_is_valid_weak_context_with_explicit_nonspatial_metadata():
    evidence = _evidence()
    evidence = replace(
        evidence,
        sampling_grid=None,
        layer=replace(
            evidence.layer,
            map=np.zeros((1, 1), dtype=np.float32),
            valid_mask=np.ones((1, 1), dtype=bool),
            strength_class="weak",
        ),
    )
    written = []
    record = write_evidence_layer(_context(written), evidence, ordinal=0)
    assert record["coordinate_space"] == "global" and record["sampling_grid"] is None
    assert record["calibration"] == "uncalibrated" and len(written) == 2
