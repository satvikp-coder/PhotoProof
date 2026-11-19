from photoproof.contracts.plugins import PluginContext


def run(ctx: PluginContext) -> dict[str, object]:
    raise RuntimeError("synthetic fixture failure, not an image measurement")
