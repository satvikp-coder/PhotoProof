"""Noise residual plugin metadata."""

from __future__ import annotations

METADATA = {
    "id": "noise.residual",
    "name": "Noise Residual Extraction",
    "description": "Wavelet-based noise residual extraction for noise-consistency analysis.",
    "version": "1.0.0",
    "api_version": "1.0.0",
    "priority": 10,
    "required": False,
    "enabled_default": True,
    "requires": (),
    "after": (),
    "entry_point": "processor:run",
    "inputs": ("image.decoded",),
    "outputs": ("noise.residual", "noise.sigma_global"),
    "timeout_seconds": 120,
    "supported_formats": (),
    "deterministic": True,
    "config_schema": "schema:Config",
    "python_dependencies": ["numpy", "PyWavelets"],
}
