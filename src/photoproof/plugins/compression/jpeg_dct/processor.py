"""Processor wiring for the JPEG DCT plugin."""

from __future__ import annotations

from photoproof.contracts.exceptions import InputRejectedError, NotApplicableError
from photoproof.contracts.plugins import PluginContext
from photoproof.plugins.compression.jpeg_dct.algorithm import analyze_jpeg_dct
from photoproof.plugins.compression.jpeg_dct.schema import Config


def run(ctx: PluginContext) -> dict[str, object]:
    """Run JPEG DCT analysis on the input bytes.

    Reads ``input.original_bytes`` and returns ``compression.jpeg_tables``
    and ``compression.dq_map``.
    """
    if "input.original_bytes" not in ctx.inputs:
        raise NotApplicableError(
            "compression.jpeg_dct requires input.original_bytes; not declared in inputs"
        )

    original_bytes = ctx.inputs["input.original_bytes"]
    if not isinstance(original_bytes, (bytes, bytearray)):
        raise InputRejectedError(
            "compression.jpeg_dct: input.original_bytes must be bytes, "
            f"got {type(original_bytes).__name__}"
        )

    config = ctx.config if isinstance(ctx.config, Config) else None
    num_ac = getattr(config, "num_ac_frequencies", 5) if config else 5
    em_iters = getattr(config, "em_iterations", 10) if config else 10
    min_blocks = getattr(config, "min_blocks", 10) if config else 10

    tables_dict, dq_map_dict = analyze_jpeg_dct(
        bytes(original_bytes),
        num_ac_frequencies=num_ac,
        em_iterations=em_iters,
        min_blocks=min_blocks,
    )

    return {
        "compression.jpeg_tables": tables_dict,
        "compression.dq_map": dq_map_dict,
    }
