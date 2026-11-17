from photoproof.contracts.plugins import PluginContext


def run(ctx: PluginContext) -> dict[str, object]:
    original = ctx.inputs["input.original_bytes"]
    assert isinstance(original, bytes)
    return {"imageprobe.original_size": len(original)}
