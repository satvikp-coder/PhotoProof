"""Noise variance plugin metadata."""

from __future__ import annotations

METADATA = {
    "id": "noise.variance_map",
    "name": "Noise Variance Map",
    "description": "Local noise variance estimation and noise-consistency mapping.",
    "version": "1.0.0",
    "api_version": "1.0.0",
    "priority": 20,
    "required": False,
    "enabled_default": True,
    "requires": ("noise.residual",),
    "after": (),
    "entry_point": "processor:run",
    "inputs": ("noise.residual", "image.decoded"),
    "outputs": ("noise.sigma_map", "noise.consistency_map", "noise.variance_summary"),
    "timeout_seconds": 60,
    "supported_formats": (),
    "deterministic": True,
    "config_schema": "schema:Config",
    "python_dependencies": ("numpy",),
}
