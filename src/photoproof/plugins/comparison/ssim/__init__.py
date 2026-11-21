"""Measured-window image similarity over verified registered material."""

METADATA = {
    "id": "comparison.ssim",
    "name": "Aligned image similarity",
    "description": "Measure structural and raw luminance differences within supported overlap.",
    "version": "1.0.0",
    "api_version": "1.11.0",
    "priority": 20,
    "required": False,
    "enabled_default": True,
    "requires": ("comparison.registration",),
    "entry_point": "processor:run",
    "inputs": (
        "input.target",
        "input.reference",
        "image.working",
        "image.geometry",
        "image.reference_geometry",
        "comparison.transform",
    ),
    "outputs": (
        "comparison.dissimilarity_map",
        "comparison.abs_diff_map",
        "comparison.similarity_validity",
        "comparison.edge_suppression",
        "comparison.similarity_summary",
    ),
    "timeout_seconds": 60,
    "deterministic": True,
    "config_schema": "schema:Config",
    "python_dependencies": ("numpy", "opencv-python-headless"),
}
