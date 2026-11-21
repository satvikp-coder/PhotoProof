"""Discrete measured differences between supplied registered inputs."""

METADATA = {
    "id": "comparison.change_mask",
    "name": "Measured comparison changes",
    "description": "Extract source-bound measured differences and bounded region statistics.",
    "version": "1.0.0",
    "api_version": "1.12.0",
    "priority": 30,
    "required": False,
    "enabled_default": True,
    "requires": ("comparison.registration", "comparison.ssim"),
    "entry_point": "processor:run",
    "inputs": (
        "comparison.transform",
        "comparison.similarity_summary",
        "image.geometry",
        "image.reference_geometry",
    ),
    "outputs": ("comparison.change_mask", "comparison.change_labels", "comparison.change_regions"),
    "timeout_seconds": 60,
    "deterministic": True,
    "config_schema": "schema:Config",
    "python_dependencies": ("numpy", "opencv-python-headless"),
}
