METADATA = {
    "id": "status.consumer",
    "name": "Execution state consumer",
    "description": "Records actual scheduler outcomes for controller tests.",
    "version": "1.0.0",
    "api_version": "1.6.0",
    "priority": 20,
    "outputs": ("probe.snapshot",),
    "inputs": ("pipeline.upstream_status",),
    "optional_inputs": ("probe.available", "probe.failed", "probe.inapplicable"),
}
