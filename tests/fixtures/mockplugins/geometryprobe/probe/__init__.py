METADATA = {
    "id": "geometry.probe",
    "name": "Geometry probe",
    "description": "Checks decoded-to-working correspondence using actual host pixels.",
    "version": "1.0.0",
    "api_version": "1.5.0",
    "priority": 10,
    "inputs": (),
    "optional_inputs": (
        "image.geometry",
        "image.decoded",
        "image.working",
        "image.reference_geometry",
    ),
    "outputs": ("geometry.observation",),
}
