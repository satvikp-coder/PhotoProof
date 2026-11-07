"""Processor wiring for the metadata.exif plugin.

Calls the pure algorithm in :mod:`photoproof.plugins.metadata.exif.algorithm`
and returns typed outputs matching ``METADATA.outputs``.
"""

from __future__ import annotations

from photoproof.contracts.exceptions import InputRejectedError, NotApplicableError
from photoproof.contracts.plugins import PluginContext
from photoproof.plugins.metadata.exif.algorithm import analyze_exif
from photoproof.plugins.metadata.exif.schema import Config


def run(ctx: PluginContext) -> dict[str, object]:
    """Extract EXIF/XMP metadata and report provenance inconsistencies.

    Reads ``input.original_bytes`` for raw EXIF parsing. Returns
    ``metadata.summary`` and ``metadata.findings``. Metadata alone is evidence
    about provenance, never about pixel authenticity.
    """
    if "input.original_bytes" not in ctx.inputs:
        raise NotApplicableError(
            "metadata.exif requires input.original_bytes; not declared in inputs"
        )

    original_bytes = ctx.inputs["input.original_bytes"]
    if not isinstance(original_bytes, (bytes, bytearray)):
        raise InputRejectedError(
            "metadata.exif: input.original_bytes must be bytes, "
            f"got {type(original_bytes).__name__}"
        )

    config = ctx.config if isinstance(ctx.config, Config) else None
    summary, findings = analyze_exif(bytes(original_bytes), config=config)

    return {
        "metadata.summary": summary,
        "metadata.findings": findings,
    }
