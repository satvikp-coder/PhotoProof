"""Synthetic mathematical score controls; detector accuracy requires actual controller tests.

These fixtures explicitly choose uniform sampling and typed source-bound arrays.
They do not claim that an arbitrary detector emitted verified forensic evidence.
"""

import numpy as np

from photoproof.contracts.evidence import EvidenceLayer
from photoproof.contracts.evidence_io import SpatialEvidence
from photoproof.contracts.grids import SamplingGrid
from photoproof.plugins.fusion.rules.native import fuse_native
from tests.unit.test_score_grid_projection import _geometry


def fuse_controls(specifications, config=None):
    spatial = [
        np.asarray(s["map"], dtype=np.float32)
        for s in specifications
        if s.get("map") is not None and "scalar_normalized" not in s.get("notes", ())
    ]
    height = max((a.shape[0] for a in spatial), default=1)
    width = max((a.shape[1] for a in spatial), default=1)
    layers = []
    for specification in specifications:
        if specification.get("map") is None:
            continue
        values = np.asarray(specification["map"], dtype=np.float32)
        notes = tuple(specification.get("notes", ()))
        global_context = "scalar_normalized" in notes
        stride = (width / values.shape[1], height / values.shape[0])
        grid = None if global_context else SamplingGrid((width, height), (0, 0), stride, stride)
        layer = EvidenceLayer(
            specification["detector_id"],
            values,
            (),
            specification["strength_class"],
            np.asarray(specification.get("valid_mask", np.ones(values.shape, bool))),
            "uncalibrated",
            notes,
        )
        layers.append(SpatialEvidence(layer, "a" * 64, grid))
    return fuse_native(layers, geometry=_geometry(width, height), config=config)


def artifact_context(plugin_id, *, config=None):
    """In-memory unit broker with real ArtifactRef schemas, hashes and exact selectors."""
    import hashlib
    import logging

    from photoproof.contracts.artifacts import ArtifactRef
    from photoproof.contracts.plugins import PluginContext

    values, blobs, written = {}, {}, []

    def put(name, payload, media_type, *, producer):
        digest = hashlib.sha256(payload).hexdigest()
        blobs[digest] = payload
        return ArtifactRef(
            name=name, sha256=digest, size=len(payload), media_type=media_type, producer=producer
        )

    def writer(name, payload, media_type):
        written.append(payload)
        return put(name, payload, media_type, producer=plugin_id)

    def reader(name, limit, selector):
        node = values[name]
        for step in selector:
            node = node[step]
        ref = (
            node if isinstance(node, ArtifactRef) else ArtifactRef.model_validate(node["artifact"])
        )
        payload = blobs[ref.sha256]
        assert len(payload) == ref.size and len(payload) <= limit
        assert hashlib.sha256(payload).hexdigest() == ref.sha256
        return payload

    ctx = PluginContext(
        run_id="control",
        plugin_id=plugin_id,
        inputs=values,
        config=config,
        seed=0,
        write_artifact=writer,
        read_artifact=reader,
        logger=logging.getLogger(__name__),
    )
    return ctx, put, written
