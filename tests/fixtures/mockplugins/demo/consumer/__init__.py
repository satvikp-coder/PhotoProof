METADATA = {
    "id": "demo.consumer",
    "name": "Demo consumer",
    "description": "Consumes the source artifact reference.",
    "version": "1.0.0",
    "api_version": "1.0.0",
    "priority": 10,
    "requires": ["demo.source"],
    "inputs": ["demo.seed_artifact"],
    "outputs": ["demo.consumed"],
}
