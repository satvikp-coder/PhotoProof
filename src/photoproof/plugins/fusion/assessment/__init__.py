"""Evidence sufficiency assessment plugin metadata (plugin API 1.2.0).

Produces the run's overall assessment category based on coverage (which detectors
were available and applicable). Missing evidence leads to ``inconclusive`` rather
than an implied clean result.
"""

from __future__ import annotations

METADATA = {
    "id": "fusion.assessment",
    "name": "Evidence Sufficiency Assessment",
    "description": "Produces the run's overall assessment category based on coverage "
    "(which detectors were available and applicable). Missing evidence leads to "
    "inconclusive rather than an implied clean result.",
    "version": "1.0.0",
    "api_version": "1.2.0",
    "priority": 95,
    "required": False,
    "enabled_default": True,
    "requires": ("fusion.rules",),
    "after": (),
    "entry_point": "processor:run",
    "inputs": (),
    "optional_inputs": ("fusion.map", "fusion.evidence"),
    "outputs": ("fusion.assessment",),
    "timeout_seconds": 120,
    "supported_formats": (),
    "deterministic": True,
    "config_schema": "schema:Config",
    "python_dependencies": ["numpy"],
}

#: Minimum number of independent detector families with non-low confidence
#: required for sufficient coverage. Provisional value, revisited in #37.
SUFFICIENT_COVERAGE_MIN_FAMILIES = 2

#: Detector families for coverage tracking.
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

#: Assessment categories. None of these contain "authentic", "genuine", or "real".
CATEGORY_MANIPULATION_INDICATED = "manipulation_indicated"
CATEGORY_DIFFERENCES_FOUND = "differences_found"
CATEGORY_NO_INDICATORS_SUFFICIENT_COVERAGE = "no_indicators_found_with_sufficient_coverage"
CATEGORY_INCONCLUSIVE = "inconclusive"

ALL_CATEGORIES = (
    CATEGORY_MANIPULATION_INDICATED,
    CATEGORY_DIFFERENCES_FOUND,
    CATEGORY_NO_INDICATORS_SUFFICIENT_COVERAGE,
    CATEGORY_INCONCLUSIVE,
)
