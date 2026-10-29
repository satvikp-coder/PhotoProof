from photoproof.contracts.plugins import PluginContext


def run(ctx: PluginContext) -> dict[str, object]:
    reference = ctx.inputs["demo.seed_artifact"]
    assert isinstance(reference, dict)
    return {"demo.consumed": reference["artifact"]["sha256"]}
