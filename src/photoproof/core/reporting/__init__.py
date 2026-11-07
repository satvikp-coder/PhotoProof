"""Forensic report generation and rendering.

Builds versioned :class:`~photoproof.core.reporting.model.Report` objects from
execution manifests and renders them to HTML or JSON.

Modules:
    :mod:`model`   — Pydantic data model for reports
    :mod:`build`   — :func:`build_report`: manifest -> Report
    :mod:`render`  — :func:`render_html`, :func:`render_json`
"""

from __future__ import annotations

from photoproof.core.reporting.build import build_report
from photoproof.core.reporting.model import Report
from photoproof.core.reporting.render import render_html, render_json

__all__ = ["Report", "build_report", "render_html", "render_json"]
