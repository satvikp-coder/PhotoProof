"""Persist segmentation without modifying upstream evidence."""

from __future__ import annotations

from photoproof.contracts.plugins import PluginContext
from photoproof.plugins.fusion.regions.native_processor import run_native


def run(ctx: PluginContext) -> dict[str, object]:
    """Run the source-bound binary production pipeline."""
    return run_native(ctx)
