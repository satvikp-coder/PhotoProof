from photoproof.contracts.plugins import PluginContext


def run(ctx: PluginContext) -> dict[str, object]:
    target, reference = ctx.inputs["input.target"], ctx.inputs["input.reference"]
    assert isinstance(target, dict) and isinstance(reference, dict)
    return {"demo.compared": target["sha256"] == reference["sha256"]}
