"""Metadata-free derived preview artifact (#10)."""

METADATA = {
    "id": "support.preview",
    "name": "Safe Preview",
    "description": "Re-encode normalized pixels as a bounded metadata-free derived PNG.",
    "version": "1.0.0",
    "api_version": "1.2.0",
    "priority": 5,
    "required": False,
    "enabled_default": True,
    "requires": (),
    "entry_point": "processor:run",
    "inputs": ("image.working",),
    "outputs": ("support.preview", "support.preview_info"),
    "config_schema": "schema:Config",
    "deterministic": True,
    "python_dependencies": ("Pillow", "numpy"),
}
