METADATA = {
    "id": "status.late",
    "name": "Execution state late",
    "description": "Records actual scheduler outcomes for controller tests.",
    "version": "1.0.0",
    "api_version": "1.6.0",
    "priority": 30,
    "outputs": ("probe.late",),
    "inputs": ("pipeline.upstream_status",),
    "requires": ("status.consumer",),
}
