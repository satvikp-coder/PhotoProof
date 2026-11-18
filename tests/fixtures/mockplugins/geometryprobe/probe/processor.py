import numpy as np

from photoproof.contracts.plugins import PluginContext


def run(ctx: PluginContext) -> dict[str, object]:
    geometry = ctx.inputs.get("image.geometry")
    if not isinstance(geometry, dict):
        return {"geometry.observation": {"available": False}}
    decoded = ctx.image("image.decoded")
    working = ctx.image("image.working")
    matrix = np.asarray(geometry["decoded_to_working"], dtype=np.int64)
    mismatches = 0
    for y in range(decoded.shape[0]):
        for x in range(decoded.shape[1]):
            wx, wy, _ = matrix @ np.asarray([x, y, 1])
            mismatches += int(not np.array_equal(decoded[y, x], working[wy, wx]))
    return {
        "geometry.observation": {
            "available": True,
            "mismatches": mismatches,
            "geometry": geometry,
            "reference_geometry": ctx.inputs.get("image.reference_geometry"),
        }
    }
