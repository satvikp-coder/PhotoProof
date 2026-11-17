"""JPEG DCT forensic plugin metadata."""

from __future__ import annotations

METADATA = {
    "id": "compression.jpeg_dct",
    "name": "JPEG DCT/Quantization Analysis",
    "description": "Quantization tables and DCT coefficient analysis for "
    "double-compression detection.",
    "version": "1.0.0",
    "api_version": "1.0.0",
    "priority": 10,
    "required": False,
    "enabled_default": True,
    "requires": (),
    "after": (),
    "entry_point": "processor:run",
    "inputs": ("input.original_bytes",),
    "outputs": ("compression.jpeg_tables", "compression.dq_map"),
    "timeout_seconds": 60,
    "supported_formats": ("jpeg",),
    "deterministic": True,
    "config_schema": "schema:Config",
    "python_dependencies": ("numpy",),
}
