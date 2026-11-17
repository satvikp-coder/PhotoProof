"""Normalize detector outputs to a common documented scale (plugin API 1.2.0).

Reads every detector output that is present in the run (declared as optional
inputs), maps each to ``EvidenceLayer`` with a score in ``[0, 1]``, and marks
absent detectors as ``unavailable`` — never as zero-score layers.
"""

from __future__ import annotations

METADATA = {
    "id": "fusion.normalize",
    "name": "Detector Output Normalization",
    "description": "Maps each detector's native output scale onto a common [0, 1] "
    "documented scale, producing EvidenceLayer objects for available detectors.",
    "version": "1.0.0",
    "api_version": "1.2.0",
    "priority": 90,
    "required": False,
    "enabled_default": True,
    "requires": (),
    "after": (),
    "entry_point": "processor:run",
    "inputs": (),
    "optional_inputs": (
        # ELA
        "compression.ela_map",
        "compression.ela_anomaly",
        "compression.ela_summary",
        # JPEG DCT
        "compression.jpeg_tables",
        "compression.dq_map",
        # JPEG grid
        "compression.grid_origin",
        "compression.grid_map",
        # Noise residual
        "noise.residual",
        "noise.sigma_global",
        "noise.residual_summary",
        # Noise variance
        "noise.sigma_map",
        "noise.consistency_map",
        "noise.variance_summary",
        # Sharpness
        "visual.sharpness_map",
        "visual.sharpness_anomaly",
        # Edges
        "visual.edge_width_map",
        "visual.edge_segments",
        # Copy-move SIFT keypoints
        "copymove.keypoints",
        "copymove.keypoint_stats",
        # Copy-move matching
        "copymove.matches",
        "copymove.match_stats",
        # Copy-move verification
        "copymove.verification",
        # Copy-move mask
        "copymove.mask",
        "copymove.mask_stats",
    ),
    "outputs": ("fusion.layers", "fusion.unavailable"),
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
DETECTOR_NORMALIZATION = {
    # ELA: weak evidence; error levels are noisy and context-dependent.
    "compression.ela_map": {
        "z0": 3.0,
        "k": 1.5,
        "strength_class": "weak",
        "rationale": "ELA error levels highlight recompression boundaries but are "
        "sensitive to save settings; weak alone, supportive with other detectors.",
    },
    "compression.ela_anomaly": {
        "z0": 3.0,
        "k": 1.5,
        "strength_class": "weak",
        "rationale": "Block-level ELA anomaly; same rationale as ela_map.",
    },
    "compression.ela_summary": {
        "z0": 3.0,
        "k": 1.5,
        "strength_class": "weak",
        "rationale": "Scalar ELA anomaly summary; same rationale as ela_map.",
    },
    # JPEG DCT: medium evidence; double-compression signatures are reliable.
    "compression.jpeg_tables": {
        "z0": 2.5,
        "k": 1.0,
        "strength_class": "medium",
        "rationale": "JPEG quantization table deviations indicate re-saving or "
        "re-compression; medium-strength forensic signal.",
    },
    "compression.dq_map": {
        "z0": 2.5,
        "k": 1.0,
        "strength_class": "medium",
        "rationale": "DQ map deviations indicate double compression; a well-understood "
        "forensic signal with low false-positive rate.",
    },
    # JPEG grid: weak evidence; grid misalignment can occur naturally.
    "compression.grid_origin": {
        "z0": 3.0,
        "k": 1.5,
        "strength_class": "weak",
        "rationale": "Grid origin offset suggests re-cropping but can occur naturally; weak alone.",
    },
    "compression.grid_map": {
        "z0": 3.0,
        "k": 1.5,
        "strength_class": "weak",
        "rationale": "8x8 grid misalignment suggests resampling but can arise from "
        "legitimate cropping; weak alone.",
    },
    # Noise residual: medium evidence; noise inconsistency is a reliable tampering cue.
    "noise.residual": {
        "z0": 2.5,
        "k": 1.0,
        "strength_class": "medium",
        "rationale": "Wavelet noise residual reveals local noise inconsistency, a "
        "reliable splicing indicator.",
    },
    "noise.sigma_global": {
        "z0": 2.5,
        "k": 1.0,
        "strength_class": "medium",
        "rationale": "Global noise sigma deviation from expected camera model; "
        "medium-strength evidence.",
    },
    "noise.residual_summary": {
        "z0": 2.5,
        "k": 1.0,
        "strength_class": "medium",
        "rationale": "Scalar noise residual summary; same rationale as noise.residual.",
    },
    # Noise variance: medium evidence; variance maps highlight smoothed regions.
    "noise.sigma_map": {
        "z0": 2.5,
        "k": 1.0,
        "strength_class": "medium",
        "rationale": "Local noise variance deviations indicate smoothing or copying; "
        "medium-strength evidence.",
    },
    "noise.consistency_map": {
        "z0": 2.5,
        "k": 1.0,
        "strength_class": "medium",
        "rationale": "Noise consistency map directly compares local noise; medium.",
    },
    "noise.variance_summary": {
        "z0": 2.5,
        "k": 1.0,
        "strength_class": "medium",
        "rationale": "Scalar variance summary; same rationale as noise.sigma_map.",
    },
    # Sharpness: weak evidence; sharpness varies naturally with focus.
    "visual.sharpness_map": {
        "z0": 3.0,
        "k": 1.5,
        "strength_class": "weak",
        "rationale": "Sharpness inconsistencies can indicate copying but also natural "
        "focus variation; weak alone.",
    },
    "visual.sharpness_anomaly": {
        "z0": 3.0,
        "k": 1.5,
        "strength_class": "weak",
        "rationale": "Block-level sharpness anomaly; same rationale as sharpness_map.",
    },
    # Edges: weak evidence; edge maps are context-dependent.
    "visual.edge_width_map": {
        "z0": 3.0,
        "k": 1.5,
        "strength_class": "weak",
        "rationale": "Edge boundary characteristics vary with image content; weak.",
    },
    "visual.edge_segments": {
        "z0": 3.0,
        "k": 1.5,
        "strength_class": "weak",
        "rationale": "Edge segment anomalies; same rationale as edge_width_map.",
    },
    # Copy-move: strong evidence; verified geometric transforms are reliable.
    "copymove.keypoints": {
        "z0": 2.0,
        "k": 0.8,
        "strength_class": "strong",
        "rationale": "SIFT keypoint matches with geometric verification indicate "
        "copy-move tampering; strong evidence when verified.",
    },
    "copymove.keypoint_stats": {
        "z0": 2.0,
        "k": 0.8,
        "strength_class": "strong",
        "rationale": "Keypoint statistics summary; same rationale as keypoints.",
    },
    "copymove.matches": {
        "z0": 2.0,
        "k": 0.8,
        "strength_class": "strong",
        "rationale": "Descriptor match clusters are strong copy-move evidence.",
    },
    "copymove.match_stats": {
        "z0": 2.0,
        "k": 0.8,
        "strength_class": "strong",
        "rationale": "Match statistics summary; same rationale as matches.",
    },
    "copymove.verification": {
        "z0": 1.5,
        "k": 0.5,
        "strength_class": "strong",
        "rationale": "RANSAC-verified affine transforms are high-confidence copy-move "
        "evidence; strongest single detector.",
    },
    "copymove.mask": {
        "z0": 1.5,
        "k": 0.5,
        "strength_class": "strong",
        "rationale": "Copy-move region masks localize tampered areas precisely.",
    },
    "copymove.mask_stats": {
        "z0": 1.5,
        "k": 0.5,
        "strength_class": "strong",
        "rationale": "Mask statistics summary; same rationale as mask.",
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
