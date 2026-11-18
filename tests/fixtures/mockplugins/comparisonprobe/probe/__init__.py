METADATA = {
    "id": "comparison.probe",
    "name": "Comparison input probe",
    "description": "Records distinguishable read-only target/reference arrays for tests.",
    "version": "1.0.0",
    "api_version": "1.3.0",
    "priority": 10,
    "inputs": ("image.working", "image.reference_working"),
    "outputs": ("comparison.observation",),
}
