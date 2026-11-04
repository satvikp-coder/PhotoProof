"""Copy-move SIFT keypoint plugin metadata."""

from __future__ import annotations

METADATA = {
    "id": "copymove.sift_keypoints",
    "name": "SIFT Keypoint Extraction",
    "description": "Extracts SIFT keypoints and descriptors for copy-move detection.",
    "version": "1.0.0",
    "api_version": "1.0.0",
    "priority": 10,
    "required": False,
    "enabled_default": True,
    "requires": (),
    "after": (),
    "entry_point": "processor:run",
    "inputs": ("image.working",),
    "outputs": ("copymove.keypoints", "copymove.keypoint_stats"),
    "timeout_seconds": 120,
    "supported_formats": (),
    "deterministic": True,
    "config_schema": "schema:Config",
    "python_dependencies": ["opencv-python-headless", "numpy"],
}
