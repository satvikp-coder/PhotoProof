import numpy as np

from photoproof.contracts.plugins import PluginContext


def run(ctx: PluginContext) -> dict[str, object]:
    target = ctx.image("image.working")
    reference = ctx.image("image.reference_working")
    return {
        "comparison.observation": {
            "target_mean": float(np.mean(target)),
            "reference_mean": float(np.mean(reference)),
            "target_shape": list(target.shape),
            "reference_shape": list(reference.shape),
            "read_only": not target.flags.writeable and not reference.flags.writeable,
        }
    }
