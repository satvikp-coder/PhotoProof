"""Copy-move mask plugin metadata."""

from __future__ import annotations

METADATA = {
    "id": "copymove.mask",
    "name": "Copy-move Region Mask",
    "description": "Generates pixel-level source/target masks from copy-move matches.",
    "version": "1.0.0",
    "api_version": "1.0.0",
    "priority": 24,
    "required": False,
    "enabled_default": True,
    "requires": ("copymove.matching", "copymove.sift_keypoints"),
    "after": (),
    "entry_point": "processor:run",
    "inputs": ("copymove.matches", "copymove.keypoints", "image.decoded"),
    "outputs": ("copymove.mask", "copymove.mask_stats"),
    "timeout_seconds": 60,
    "supported_formats": (),
    "deterministic": True,
    "config_schema": "schema:Config",
    "python_dependencies": ["numpy"],
}
