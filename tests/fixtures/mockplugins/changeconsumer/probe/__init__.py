METADATA = {
    "id": "comparison.change_probe",
    "name": "Change consumer probe",
    "description": "Validate actual measured labels, PNG and region records through the broker.",
    "version": "1.0.0",
    "api_version": "1.12.0",
    "priority": 40,
    "requires": ("comparison.registration", "comparison.ssim", "comparison.change_mask"),
    "entry_point": "processor:run",
    "inputs": (
        "comparison.transform",
        "comparison.similarity_summary",
        "comparison.change_regions",
        "image.geometry",
        "image.reference_geometry",
    ),
    "outputs": ("comparison.change_probe",),
}
