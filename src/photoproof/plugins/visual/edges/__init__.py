"""Visual edges plugin metadata."""

from __future__ import annotations

METADATA = {
    "id": "visual.edges",
    "name": "Visual Edges",
    "description": "Characterizes object boundaries with Sobel and Canny edge analysis.",
    "version": "1.0.0",
    "api_version": "1.0.0",
    "priority": 10,
    "required": False,
    "enabled_default": True,
    "requires": (),
    "after": (),
    "entry_point": "processor:run",
    "inputs": ("image.working",),
    "outputs": ("visual.edge_width_map", "visual.edge_segments"),
    "timeout_seconds": 60,
    "supported_formats": (),
    "deterministic": True,
    "config_schema": "schema:Config",
    "python_dependencies": ["opencv-python-headless", "numpy"],
}
