"""Clean suspicious regions from fused heuristic evidence (#35)."""

METADATA = {
    "id": "fusion.regions",
    "name": "Region Segmentation",
    "description": "Segment fused heuristic scores with hysteresis and morphological cleanup.",
    "version": "1.0.0",
    "api_version": "1.2.0",
    "priority": 100,
    "required": False,
    "enabled_default": True,
    "requires": ("fusion.rules",),
    "entry_point": "processor:run",
    "inputs": ("fusion.map", "fusion.evidence"),
    "outputs": ("fusion.mask", "fusion.regions", "fusion.segmentation"),
    "timeout_seconds": 120,
    "deterministic": True,
    "config_schema": "schema:Config",
    "python_dependencies": ("numpy", "opencv-python-headless"),
}
