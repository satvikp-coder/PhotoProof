from photoproof.contracts.plugins import PluginContext


def run(ctx: PluginContext) -> dict[str, object]:
    return {"probe.snapshot": ctx.inputs["pipeline.upstream_status"]}
