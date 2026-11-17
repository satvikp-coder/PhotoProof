import numpy as np

from photoproof.contracts.plugins import PluginContext


def run(ctx: PluginContext) -> dict[str, object]:
    working = ctx.inputs["image.working"]
    assert isinstance(working, np.ndarray)
    return {
        "imageprobe.working_shape": [int(size) for size in working.shape],
        "imageprobe.working_read_only": not working.flags.writeable,
    }
