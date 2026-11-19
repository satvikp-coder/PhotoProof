METADATA = {
    "id": "evidence.producer",
    "name": "Binary layer producer probe",
    "description": "Persists measured pixel scores for transport tests only.",
    "version": "1.0.0",
    "api_version": "1.5.0",
    "priority": 10,
    "inputs": ("image.decoded", "image.geometry"),
    "outputs": ("evidence.layers",),
}
