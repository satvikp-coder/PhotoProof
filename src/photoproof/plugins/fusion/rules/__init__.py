"""Rule-based evidence fusion plugin metadata (plugin API 1.2.0).

Combines normalized evidence layers from ``fusion.normalize`` into a fused
suspicion map and per-region evidence summaries using transparent, documented
rules. It is not a learned or probabilistic model — scores are heuristic and
explicitly labelled as such.
"""

from __future__ import annotations

METADATA = {
    "id": "fusion.rules",
    "name": "Rule-Based Evidence Fusion",
    "description": "Combines normalized evidence layers into a fused suspicion "
    "map and per-region evidence summaries using transparent, documented rules.",
    "version": "1.0.0",
    "api_version": "1.2.0",
    "priority": 90,
    "required": False,
    "enabled_default": True,
    "requires": ("fusion.normalize",),
    "after": (),
    "entry_point": "processor:run",
    "inputs": (),
    "optional_inputs": ("fusion.layers", "fusion.unavailable"),
    "outputs": ("fusion.map", "fusion.evidence"),
    "timeout_seconds": 120,
    "supported_formats": (),
    "deterministic": True,
    "config_schema": "schema:Config",
    "python_dependencies": ["numpy"],
}

#: Weight applied to each strength class in the weighted noisy-OR combination.
#: Strong layers contribute directly (weight 1.0 via max), medium layers are
#: gated, weak layers are gated more strictly.
STRENGTH_WEIGHTS = {
    "strong": 1.0,
    "medium": 0.6,
    "weak": 0.3,
}

#: A medium layer is considered "high score" on its own if it exceeds this.
#: A high-scoring medium layer can mark a region suspicious without support
#: from another family.
MEDIUM_HIGH_SCORE_THRESHOLD = 0.8

#: A weak layer raises confidence only where medium/strong evidence exists.
#: The weak boost factor is applied to the existing fused score.
WEAK_BOOST_FACTOR = 0.2

#: The maximum score a weak-only combination can reach. Weak evidence alone
#: must never reach "suspicious" (>= 0.5).
WEAK_ONLY_MAX_SCORE = 0.4
