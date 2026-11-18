import numpy as np

from photoproof.contracts.evidence import EvidenceLayer
from photoproof.contracts.evidence_io import SpatialEvidence, write_evidence_layer
from photoproof.contracts.grids import SamplingGrid
from photoproof.contracts.plugins import PluginContext


def run(ctx: PluginContext) -> dict[str, object]:
    image = ctx.image("image.decoded")
    geometry = ctx.inputs["image.geometry"]
    scores = image[..., 0].astype(np.float32) / 255
    layer = EvidenceLayer(
        detector_id="probe.measured_pixels",
        map=scores,
        regions=(),
        strength_class="weak",
        valid_mask=np.ones(scores.shape, dtype=bool),
        calibration="uncalibrated",
        notes=("transport_test_not_forensic_evidence",),
    )
    evidence = SpatialEvidence(
        layer,
        geometry["source_sha256"],
        SamplingGrid(tuple(geometry["decoded_size"]), (0, 0), (1, 1), (1, 1)),
    )
    return {"evidence.layers": [write_evidence_layer(ctx, evidence, ordinal=0)]}
