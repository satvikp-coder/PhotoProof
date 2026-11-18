METADATA = {
    "id": "artifactprobe.consumer",
    "name": "Array consumer",
    "description": "Reads a verified declared array input.",
    "version": "1.0.0",
    "api_version": "1.4.0",
    "priority": 20,
    "requires": ("artifactprobe.producer",),
    "inputs": ("artifactprobe.array", "artifactprobe.layers"),
    "outputs": ("artifactprobe.mean", "artifactprobe.shape"),
}
