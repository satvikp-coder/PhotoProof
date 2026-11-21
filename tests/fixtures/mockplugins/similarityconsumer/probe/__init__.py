METADATA = {
    "id": "comparison.similarity_probe",
    "name": "Similarity consumer probe",
    "description": "Read measured maps through the declared artifact broker.",
    "version": "1.0.0",
    "api_version": "1.11.0",
    "priority": 30,
    "requires": ("comparison.ssim", "comparison.registration"),
    "entry_point": "processor:run",
    "inputs": (
        "comparison.transform",
        "comparison.similarity_summary",
        "image.geometry",
        "image.reference_geometry",
    ),
    "outputs": ("comparison.similarity_probe",),
}
