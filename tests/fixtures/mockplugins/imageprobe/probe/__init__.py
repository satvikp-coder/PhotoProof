METADATA = {
    "id": "imageprobe.mean",
    "name": "Image probe",
    "description": "Reports basic statistics of the decoded image it receives.",
    "version": "1.0.0",
    "api_version": "1.0.0",
    "priority": 10,
    "inputs": ["image.decoded"],
    "outputs": ["imageprobe.mean", "imageprobe.shape", "imageprobe.read_only"],
    "python_dependencies": ["numpy"],
}
