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
    "version": "2.0.0",
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
    # JPEG DCT: provisional medium cue; benign re-saving is a confounder.
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
        "rationale": "DQ deviations may indicate recompression, including benign re-saving; "
        "provisional medium strength is not a validated false-positive guarantee.",
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
    # Raw residuals and absolute levels are context; measured inconsistency is separate.
    "noise.residual": {
        "z0": 2.5,
        "k": 1.0,
        "strength_class": "weak",
        "rationale": "Raw residuals, absolute noise levels and summaries do not establish "
        "spatial noise inconsistency; weak context pending measured consistency evidence.",
    },
    "noise.sigma_global": {
        "z0": 2.5,
        "k": 1.0,
        "strength_class": "weak",
        "rationale": "Raw residuals, absolute noise levels and summaries do not establish "
        "spatial noise inconsistency; weak context pending measured consistency evidence.",
    },
    "noise.residual_summary": {
        "z0": 2.5,
        "k": 1.0,
        "strength_class": "weak",
        "rationale": "Raw residuals, absolute noise levels and summaries do not establish "
        "spatial noise inconsistency; weak context pending measured consistency evidence.",
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
        "strength_class": "weak",
        "rationale": "Raw residuals, absolute noise levels and summaries do not establish "
        "spatial noise inconsistency; weak context pending measured consistency evidence.",
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
    # Verified geometric repetition is a strong candidate cue, not standalone proof.
    "copymove.keypoints": {
        "z0": 2.0,
        "k": 0.8,
        "strength_class": "weak",
        "rationale": "Unverified features, counts or summary statistics are context only; "
        "they do not establish geometrically verified copy-move localization.",
    },
    "copymove.keypoint_stats": {
        "z0": 2.0,
        "k": 0.8,
        "strength_class": "weak",
        "rationale": "Unverified features, counts or summary statistics are context only; "
        "they do not establish geometrically verified copy-move localization.",
    },
    "copymove.matches": {
        "z0": 2.0,
        "k": 0.8,
        "strength_class": "weak",
        "rationale": "Unverified features, counts or summary statistics are context only; "
        "they do not establish geometrically verified copy-move localization.",
    },
    "copymove.match_stats": {
        "z0": 2.0,
        "k": 0.8,
        "strength_class": "weak",
        "rationale": "Unverified features, counts or summary statistics are context only; "
        "they do not establish geometrically verified copy-move localization.",
    },
    "copymove.verification": {
        "z0": 1.5,
        "k": 0.5,
        "strength_class": "strong",
        "rationale": "Geometrically verified repeated-region correspondences can indicate copying; "
        "repeated scene structure remains a confounder.",
    },
    "copymove.mask": {
        "method": "identity",
        "z0": 1.5,
        "k": 0.5,
        "strength_class": "strong",
        "rationale": "Derived localization of geometrically verified candidate copy-move regions; "
        "inspect repetition and global-processing limitations.",
    },
    "copymove.mask_stats": {
        "z0": 1.5,
        "k": 0.5,
        "strength_class": "weak",
        "rationale": "Unverified features, counts or summary statistics are context only; "
        "they do not establish geometrically verified copy-move localization.",
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
