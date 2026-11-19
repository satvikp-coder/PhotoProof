"""Copy-move verification plugin metadata."""

from __future__ import annotations

METADATA = {
    "id": "copymove.verification",
    "name": "Copy-move Verification",
    "description": "Seeded geometry and inlier identities with actual-pixel texture cautions.",
    "version": "2.0.0",
    "api_version": "1.8.0",
    "priority": 40,
    "required": False,
    "enabled_default": True,
    "requires": ("copymove.clustering", "copymove.matching", "copymove.sift_keypoints"),
    "after": (),
    "entry_point": "processor:run",
    "inputs": (
        "copymove.clusters",
        "copymove.matches",
        "copymove.keypoints",
        "image.geometry",
        "image.working",
    ),
    "outputs": (
        "copymove.verified",
        "copymove.verified_inliers",
        "copymove.verification_bindings",
        "copymove.verification_details",
        "copymove.verification_stats",
    ),
    "timeout_seconds": 60,
    "supported_formats": (),
    "deterministic": True,
    "config_schema": "schema:Config",
    "python_dependencies": ["opencv-python-headless", "numpy"],
}
