from photoproof.contracts.plugins import PluginContext


def run(ctx: PluginContext) -> dict[str, object]:
    target = ctx.inputs["input.target"]
    assert isinstance(target, dict)
    data = f"{target['sha256']}:{ctx.seed}".encode()
    return {"demo.seed_artifact": ctx.write_artifact("seed.txt", data, "text/plain")}
