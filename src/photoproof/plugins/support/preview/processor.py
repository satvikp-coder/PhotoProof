"""Persist only derived pixels through the framework artifact writer."""

from photoproof.contracts.plugins import PluginContext
from photoproof.plugins.support.preview.algorithm import encode_preview
from photoproof.plugins.support.preview.schema import Config


def run(ctx: PluginContext) -> dict[str, object]:
    """Produce a display-only preview; original evidence is never read or replaced."""
    config = ctx.config if isinstance(ctx.config, Config) else Config()
    data, width, height = encode_preview(ctx.image("image.working"), max_edge=config.max_edge)
    return {
        "support.preview": ctx.write_artifact("derived-preview.png", data, "image/png"),
        "support.preview_info": {
            "kind": "derived_preview",
            "evidence": False,
            "width": width,
            "height": height,
            "alpha": "retained",
            "metadata": "removed",
            "max_edge": config.max_edge,
            "label": "Derived preview - not original evidence",
        },
    }
