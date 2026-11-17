"""Copy-move verification plugin metadata."""

from __future__ import annotations

METADATA = {
    "id": "copymove.verification",
    "name": "Copy-move Verification",
    "description": "RANSAC geometric verification of copy-move clusters.",
    "version": "1.0.0",
    "api_version": "1.0.0",
    "priority": 30,
    "required": False,
    "enabled_default": True,
    "requires": ("copymove.matching", "copymove.sift_keypoints"),
    "after": (),
    "entry_point": "processor:run",
    "inputs": ("copymove.matches", "copymove.keypoints"),
    "outputs": ("copymove.verification",),
    "timeout_seconds": 60,
    "supported_formats": (),
    "deterministic": True,
    "config_schema": "schema:Config",
    "python_dependencies": ["opencv-python-headless", "numpy"],
}
