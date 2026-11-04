"""EXIF metadata forensic plugin.

Extracts EXIF, XMP and container metadata from JPEG, PNG and WebP images, and
reports documented inconsistency findings as low-weight provenance evidence.

Metadata is trivially editable and often stripped. Findings are reported as
context, never as proof of manipulation. GPS coordinates are never emitted.
"""

from __future__ import annotations

METADATA = {
    "id": "metadata.exif",
    "name": "EXIF Metadata Inspection",
    "description": "Extracts EXIF/XMP metadata and reports provenance inconsistencies.",
    "version": "1.0.0",
    "api_version": "1.0.0",
    "priority": 10,
    "required": False,
    "enabled_default": True,
    "requires": (),
    "after": (),
    "entry_point": "processor:run",
    "inputs": ("input.original_bytes", "image.working"),
    "outputs": ("metadata.summary", "metadata.findings"),
    "timeout_seconds": 30,
    "supported_formats": (),
    "deterministic": True,
    "config_schema": "schema:Config",
    "python_dependencies": ("Pillow",),
}
