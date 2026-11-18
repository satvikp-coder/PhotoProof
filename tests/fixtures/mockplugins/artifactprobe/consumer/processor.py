import io

import numpy as np

from photoproof.contracts.plugins import PluginContext


def run(ctx: PluginContext) -> dict[str, object]:
    payload = ctx.artifact("artifactprobe.array")
    assert payload == ctx.artifact("artifactprobe.layers", selector=(0, "map"))
    values = np.load(io.BytesIO(payload), allow_pickle=False)
    return {"artifactprobe.mean": float(values.mean()), "artifactprobe.shape": list(values.shape)}
