"""Visual sharpness plugin metadata.

No ``METADATA`` here; individual detector plugins live in sub-packages
such as ``visual.sharpness``.
"""

from __future__ import annotations

METADATA = {
    "id": "visual.sharpness",
    "name": "Visual Sharpness",
    "description": "Maps local focus and sharpness with the variance of the Laplacian.",
    "version": "1.0.0",
    "api_version": "1.0.0",
    "priority": 10,
    "required": False,
    "enabled_default": True,
    "requires": (),
    "after": (),
    "entry_point": "processor:run",
    "inputs": ("image.working",),
    "outputs": ("visual.sharpness_map", "visual.sharpness_anomaly"),
    "timeout_seconds": 60,
    "supported_formats": (),
    "deterministic": True,
    "config_schema": "schema:Config",
    "python_dependencies": ["opencv-python-headless", "numpy"],
}
