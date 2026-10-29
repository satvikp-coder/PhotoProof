METADATA = {
    "id": "demo.observer",
    "name": "Demo observer",
    "description": "Runs after the consumer regardless of its outcome.",
    "version": "1.0.0",
    "api_version": "1.0.0",
    "priority": 20,
    "after": ["demo.consumer", "demo.configurable"],
    "outputs": ["demo.observed"],
}
