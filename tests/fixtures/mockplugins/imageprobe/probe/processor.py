import numpy as np

from photoproof.contracts.plugins import PluginContext


def run(ctx: PluginContext) -> dict[str, object]:
    image = ctx.inputs["image.decoded"]
    assert isinstance(image, np.ndarray)
    return {
        "imageprobe.mean": float(image.mean()),
        "imageprobe.shape": [int(size) for size in image.shape],
        "imageprobe.read_only": not image.flags.writeable,
    }
