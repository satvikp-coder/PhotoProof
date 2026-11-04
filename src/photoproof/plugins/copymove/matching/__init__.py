"""Copy-move SIFT descriptor matching plugin metadata."""

from __future__ import annotations

METADATA = {
    "id": "copymove.matching",
    "name": "SIFT Descriptor Matching",
    "description": "Matches SIFT descriptors using KNN and generalized Lowe ratio filtering.",
    "version": "1.0.0",
    "api_version": "1.0.0",
    "priority": 20,
    "required": False,
    "enabled_default": True,
    "requires": ("copymove.sift_keypoints",),
    "after": (),
    "entry_point": "processor:run",
    "inputs": ("copymove.keypoints",),
    "outputs": ("copymove.matches", "copymove.match_stats"),
    "timeout_seconds": 120,
    "supported_formats": (),
    "deterministic": True,
    "config_schema": "schema:Config",
    "python_dependencies": ["opencv-python-headless", "numpy"],
}
