"""Normalize actual native evidence with explicit source/sampling validity (API 1.9).

Reads every detector output that is present in the run (declared as optional
inputs), maps each to ``EvidenceLayer`` with a score in ``[0, 1]``, and marks
absent detectors as ``unavailable`` — never as zero-score layers.
"""

from __future__ import annotations

METADATA = {
    "id": "fusion.normalize",
    "name": "Detector Output Normalization",
    "description": "Maps each detector's native output scale onto a common [0, 1] documented "
    "scale, producing EvidenceLayer objects for available detectors.",
    "version": "3.0.0",
    "api_version": "1.9.0",
    "priority": 90,
    "required": False,
    "enabled_default": True,
    "requires": (),
    "after": (),
    "entry_point": "processor:run",
    "inputs": (),
    "optional_inputs": (
        "compression.ela_map",
        "compression.ela_anomaly",
        "compression.ela_summary",
        "compression.jpeg_tables",
        "compression.dq_map",
        "compression.grid_origin",
        "compression.grid_map",
        "noise.residual",
        "noise.sigma_global",
        "noise.residual_summary",
        "noise.sigma_map",
        "noise.consistency_map",
        "noise.variance_summary",
        "visual.sharpness_map",
        "visual.sharpness_anomaly",
        "visual.edge_width_map",
        "visual.edge_segments",
        "copymove.keypoints",
        "copymove.keypoint_stats",
        "copymove.matches",
        "copymove.match_stats",
        "copymove.verification",
        "copymove.mask",
        "copymove.mask_stats",
        "image.geometry",
        "pipeline.upstream_status",
        "copymove.mask_metadata",
        "copymove.correlation_map",
        "copymove.correlation_validity",
        "copymove.regions",
        "copymove.verification_bindings",
        "copymove.verified",
        "copymove.verified_inliers",
    ),
    "outputs": ("fusion.layers", "fusion.unavailable", "fusion.availability"),
    "timeout_seconds": 120,
    "supported_formats": (),
    "deterministic": True,
    "config_schema": "schema:Config",
    "python_dependencies": ["numpy"],
}

#: Per-detector normalization parameters and strength class.
#: Each entry maps a detector output contract to its normalization config.
#: ``z0`` and ``k`` define the logistic squashing ``s = 1 / (1 + exp(-(z - z0) / k))``
#: applied to robust z-scores. ``strength_class`` is one of ``weak``, ``medium``,
#: ``strong``. Scores remain uncalibrated heuristics until #37 provides calibration
#: data.
NATIVE_OUTPUTS = (
    "compression.ela_anomaly",
    "compression.dq_map",
    "compression.grid_map",
    "noise.consistency_map",
    "copymove.mask",
)

CONTEXT_OUTPUTS = (
    "compression.ela_map",
    "compression.ela_summary",
    "compression.grid_origin",
    "compression.jpeg_tables",
    "copymove.keypoint_stats",
    "copymove.keypoints",
    "copymove.mask_stats",
    "copymove.match_stats",
    "copymove.matches",
    "copymove.verification",
    "noise.residual",
    "noise.residual_summary",
    "noise.sigma_global",
    "noise.sigma_map",
    "noise.variance_summary",
    "visual.edge_segments",
    "visual.edge_width_map",
    "visual.sharpness_anomaly",
    "visual.sharpness_map",
)

DETECTOR_NORMALIZATION = {
    "compression.ela_anomaly": {
        "z0": 3.0,
        "k": 1.5,
        "strength_class": "weak",
        "rationale": "Block-level ELA anomaly; same rationale as ela_map.",
    },
    "compression.dq_map": {
        "z0": 2.5,
        "k": 1.0,
        "strength_class": "medium",
        "rationale": "DQ deviations may indicate recompression, including "
        "benign re-saving; provisional medium strength is not a "
        "validated false-positive guarantee.",
    },
    "compression.grid_map": {
        "z0": 3.0,
        "k": 1.5,
        "strength_class": "weak",
        "rationale": "8x8 grid misalignment suggests resampling but can "
        "arise from legitimate cropping; weak alone.",
    },
    "noise.consistency_map": {
        "z0": 2.5,
        "k": 1.0,
        "strength_class": "medium",
        "rationale": "Noise consistency map directly compares local noise; medium.",
    },
    "copymove.mask": {
        "method": "identity",
        "z0": 1.5,
        "k": 0.5,
        "strength_class": "strong",
        "rationale": "Derived localization of geometrically verified candidate "
        "copy-move regions; inspect repetition and global-processing "
        "limitations.",
    },
}

#: Detector families for family counting in fusion rules (#33).
#: Correlated detectors within a family count once toward evidence strength.
DETECTOR_FAMILIES = {
    "compression.ela_map": "compression",
    "compression.ela_anomaly": "compression",
    "compression.ela_summary": "compression",
    "compression.jpeg_tables": "compression",
    "compression.dq_map": "compression",
    "compression.grid_origin": "compression",
    "compression.grid_map": "compression",
    "noise.residual": "noise",
    "noise.sigma_global": "noise",
    "noise.residual_summary": "noise",
    "noise.sigma_map": "noise",
    "noise.consistency_map": "noise",
    "noise.variance_summary": "noise",
    "visual.sharpness_map": "visual",
    "visual.sharpness_anomaly": "visual",
    "visual.edge_width_map": "visual",
    "visual.edge_segments": "visual",
    "copymove.keypoints": "geometry",
    "copymove.keypoint_stats": "geometry",
    "copymove.matches": "geometry",
    "copymove.match_stats": "geometry",
    "copymove.verification": "geometry",
    "copymove.mask": "geometry",
    "copymove.mask_stats": "geometry",
}

#: Every layer carries this calibration until #37 provides calibration data.
UNCALIBRATED = "uncalibrated"
