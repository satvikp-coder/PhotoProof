METADATA = {
    "id": "comparison.registration_probe",
    "name": "Registration consumer probe",
    "description": "Observe actual registration payloads through the declared broker.",
    "version": "1.0.0",
    "api_version": "1.10.0",
    "priority": 20,
    "requires": ("comparison.registration",),
    "entry_point": "processor:run",
    "inputs": ("comparison.transform", "image.geometry", "image.reference_geometry"),
    "outputs": ("comparison.registration_probe",),
}
