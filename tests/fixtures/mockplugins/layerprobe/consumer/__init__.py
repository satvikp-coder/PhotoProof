METADATA = {
    "id": "evidence.consumer",
    "name": "Binary layer consumer probe",
    "description": "Reads and projects actual binary layer artifacts for transport tests.",
    "version": "1.0.0",
    "api_version": "1.5.0",
    "priority": 20,
    "requires": ("evidence.producer",),
    "inputs": ("evidence.layers", "image.geometry"),
    "outputs": ("evidence.observation",),
}
