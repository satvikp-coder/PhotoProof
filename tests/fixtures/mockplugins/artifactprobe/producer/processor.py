import io

import numpy as np

from photoproof.contracts.plugins import PluginContext


def run(ctx: PluginContext) -> dict[str, object]:
    buffer = io.BytesIO()
    np.save(buffer, ctx.image("image.working").astype(np.float32), allow_pickle=False)
    ref = ctx.write_artifact("pixels.npy", buffer.getvalue(), "application/x-npy")
    return {"artifactprobe.array": ref, "artifactprobe.layers": [{"map": ref}]}
