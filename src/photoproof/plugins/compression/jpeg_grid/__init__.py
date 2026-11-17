"""JPEG grid forensic plugin metadata."""

from __future__ import annotations

METADATA = {
    "id": "compression.jpeg_grid",
    "name": "JPEG 8x8 Grid Misalignment",
    "description": "Blocking artifact grid analysis for grid-origin "
    "estimation and misalignment detection.",
    "version": "1.0.0",
    "api_version": "1.0.0",
    "priority": 10,
    "required": False,
    "enabled_default": True,
    "requires": (),
    "after": (),
    "entry_point": "processor:run",
    "inputs": ("image.decoded",),
    "outputs": ("compression.grid_origin", "compression.grid_map"),
    "timeout_seconds": 60,
    "supported_formats": (),
    "deterministic": True,
    "config_schema": "schema:Config",
    "python_dependencies": ("numpy",),
}
