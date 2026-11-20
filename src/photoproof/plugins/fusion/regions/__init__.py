"""Clean suspicious regions from fused heuristic evidence (#35)."""

METADATA = {
    "id": "fusion.regions",
    "name": "Region Segmentation",
    "description": "Segment fused heuristic scores with hysteresis and morphological cleanup.",
    "version": "3.1.0",
    "api_version": "1.9.0",
    "priority": 100,
    "required": False,
    "enabled_default": True,
    "requires": (),
    "entry_point": "processor:run",
    "inputs": (),
    "outputs": ("fusion.mask", "fusion.regions", "fusion.segmentation"),
    "timeout_seconds": 120,
    "deterministic": True,
    "config_schema": "schema:Config",
    "python_dependencies": ("numpy", "opencv-python-headless"),
    "after": ("fusion.rules",),
    "optional_inputs": ("fusion.map", "fusion.evidence", "image.geometry"),
}
