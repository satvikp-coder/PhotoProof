"""Traceable, uncalibrated heuristic visualization (#36)."""

METADATA = {
    "id": "fusion.heatmap",
    "name": "Forensic Heatmap",
    "description": "Render heuristic scores on a verified derived safe preview.",
    "version": "1.0.0",
    "api_version": "1.2.0",
    "priority": 100,
    "required": False,
    "enabled_default": True,
    "requires": ("fusion.regions", "support.preview", "fusion.rules"),
    "entry_point": "processor:run",
    "inputs": (
        "image.working",
        "fusion.map",
        "fusion.regions",
        "support.preview",
        "support.preview_info",
    ),
    "outputs": ("fusion.heatmap", "fusion.overlay", "fusion.outlines", "fusion.legend"),
    "config_schema": "schema:Config",
    "deterministic": True,
    "python_dependencies": ("Pillow", "numpy", "opencv-python-headless"),
}
