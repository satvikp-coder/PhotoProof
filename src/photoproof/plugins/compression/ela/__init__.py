"""Error Level Analysis plugin metadata."""

from __future__ import annotations

METADATA = {
    "id": "compression.ela",
    "name": "Error Level Analysis",
    "description": "JPEG recompression residual map; supporting signal only.",
    "version": "1.0.0",
    "api_version": "1.0.0",
    "priority": 10,
    "required": False,
    "enabled_default": True,
    "requires": (),
    "after": (),
    "entry_point": "processor:run",
    "inputs": ("image.decoded",),
    "outputs": ("compression.ela_map", "compression.ela_anomaly"),
    "timeout_seconds": 60,
    "supported_formats": ("jpeg",),
    "deterministic": True,
    "config_schema": "schema:Config",
    "python_dependencies": ("Pillow", "numpy"),
}
