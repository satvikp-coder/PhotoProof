"""Source-bound geometric registration of user-supplied reference material."""

METADATA = {
    "id": "comparison.registration",
    "name": "Reference registration",
    "description": "Estimate validated reference-to-target geometry and measured overlap.",
    "version": "1.0.0",
    "api_version": "1.10.0",
    "priority": 10,
    "required": False,
    "enabled_default": True,
    "requires": (),
    "entry_point": "processor:run",
    "inputs": (
        "input.target",
        "input.reference",
        "image.working",
        "image.reference_working",
        "image.geometry",
        "image.reference_geometry",
    ),
    "outputs": (
        "comparison.transform",
        "comparison.aligned_reference",
        "comparison.overlap_mask",
        "comparison.inlier_points",
    ),
    "timeout_seconds": 120,
    "deterministic": True,
    "config_schema": "schema:Config",
    "python_dependencies": ("numpy", "opencv-python-headless"),
}
